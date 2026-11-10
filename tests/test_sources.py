"""File ingestion and the file-to-review graph, without network or browser/model calls."""

import io
import json

import pypdfium2 as pdfium
import pytest
from PIL import Image, PngImagePlugin

from app.pipeline import graph, runtime, sources
from app.pipeline.llm import FakeLLM
from app.pipeline.schemas import CompanyProfile


def image_bytes(format="PNG", size=(40, 30)):
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "do not retain")
    Image.new("RGB", size, "orange").save(buffer, format, pnginfo=metadata)
    return buffer.getvalue()


def pdf_bytes(pages=1):
    buffer = io.BytesIO()
    with sources.PDF_LOCK, pdfium.PdfDocument.new() as pdf:
        for _ in range(pages):
            pdf.new_page(300, 400).close()
        pdf.save(buffer)
    return buffer.getvalue()


def text_pdf(text="Made for everyday adventures."):
    stream = f"BT /F1 24 Tf 30 100 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    document, offsets = b"%PDF-1.4\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(document)
    document += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    document += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    document += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return document


def illustrated_pdf():
    buffer = io.BytesIO()
    with sources.PDF_LOCK, pdfium.PdfDocument(text_pdf()) as pdf:
        page = pdf[0]
        bitmap = pdfium.PdfBitmap.from_pil(Image.new("RGB", (120, 80), "orange"))
        try:
            obj = pdfium.PdfImage.new(pdf)
            obj.set_bitmap(bitmap)
            obj.set_matrix(pdfium.PdfMatrix(120, 0, 0, 80, 20, 10))
            page.insert_obj(obj)
            page.gen_content()
            pdf.save(buffer)
        finally:
            page.close()
            bitmap.close()
    return buffer.getvalue()


@pytest.mark.parametrize("format,name", [("PNG", "photo.png"), ("JPEG", "photo.jpg"), ("WEBP", "photo.webp")])
def test_images_are_bounded_normalised_and_metadata_free(format, name):
    [(meta, content)] = sources.validate_sources([(f"../private/{name}", image_bytes(format, (3000, 20)))])
    assert meta == {"name": name, "file": "sources/01.png", "pages": 0}
    with Image.open(io.BytesIO(content)) as image:
        assert image.format == "PNG" and image.width == 2048 and "private" not in image.info


def test_exif_icc_are_removed_and_orientation_is_applied():
    buffer = io.BytesIO()
    image = Image.new("RGB", (30, 50), "orange")
    exif = Image.Exif()
    exif[274], exif[315] = 6, "private author"
    image.save(buffer, "JPEG", exif=exif, icc_profile=b"private colour profile")
    [(_, content)] = sources.validate_sources([("portrait.jpg", buffer.getvalue())])
    with Image.open(io.BytesIO(content)) as result:
        assert result.size == (50, 30)
        assert "exif" not in result.info and "icc_profile" not in result.info


def test_vision_input_composites_transparency_on_white(tmp_path):
    import base64
    from app.pipeline.llm import image_block

    file = tmp_path / "transparent.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(file)
    with Image.open(io.BytesIO(base64.b64decode(image_block(file)["data"]))) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_pdf_validation_and_batch_limits():
    content = pdf_bytes(2)
    [(meta, result)] = sources.validate_sources([("brochure.pdf", content)])
    assert meta["pages"] == 2 and result == content
    for files, message in [([], "1–10"), ([("a.png", image_bytes())] * 11, "1–10"),
                           ([("a.pdf", b"%PDF-invalid")], "valid PDF"),
                           ([("a.pdf", b"not pdf")], "valid"),
                           ([("a.svg", b"<svg/>")], "PNG"),
                           ([("a.png", b"broken")], "valid PNG"),
                           ([("a.png", b"")], "nonempty"),
                           ([("a.pdf", b"x" * (sources.MAX_TOTAL_BYTES + 1))], "100 MB")]:
        with pytest.raises(ValueError, match=message):
            sources.validate_sources(files)


def test_pixel_limit_and_path_validation(tmp_path):
    with pytest.raises(ValueError, match="20 megapixels"):
        sources.validate_sources([("large.png", image_bytes(size=(5000, 4001)))])
    with pytest.raises(ValueError, match="path"):
        sources.prepare_sources(tmp_path, [{"name": "image.png", "file": "../image.png"}])


def store_sources(root, files):
    validated = sources.validate_sources(files)
    for meta, content in validated:
        path = root / meta["file"]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    return [meta for meta, _ in validated]


async def test_native_pdf_text_and_scans_keep_page_provenance(tmp_path):
    scan = io.BytesIO()
    Image.new("RGB", (200, 300), "blue").save(scan, "PDF")
    files = store_sources(tmp_path, [("brochure.pdf", text_pdf()), ("scan.pdf", scan.getvalue())])
    llm = FakeLLM({"source": [
        {"transcript": "Made for everyday adventures.", "visual_description": "Black typography on white paper."},
        {"transcript": "Visible words from a scan", "visual_description": "A blue page."},
    ], "source_image": [{"visual_description": "A blue image."}]})
    await sources.read_sources(tmp_path, files, llm, lambda _: None)
    crawl = sources.CrawlResult.load(tmp_path / "crawl")
    assert [p.url for p in crawl.pages] == ["sources/01.pdf#page=1", "sources/02.pdf#page=1"]
    assert "Made for everyday adventures." in crawl.pages[0].data["source_text"]
    assert crawl.pages[0].data["text_method"] == "pdf"
    assert crawl.pages[0].data["ocr_text"] == ""
    assert crawl.pages[1].data["source_text"] == "Visible words from a scan"
    assert crawl.pages[1].data["text_method"] == "ocr"
    assert all((tmp_path / "brand" / page.data["asset"]).is_file() for page in crawl.pages)
    assert all(any(block["type"] == "image" for block in call[1][1].content) for call in llm.calls)

    profile = CompanyProfile(company_name="", language="en", one_liner="", audience="", tone="", facts=[
        {"id": "f1", "kind": "claim", "text": "Made for everyday adventures.", "quote": "Made for everyday adventures.", "source_url": crawl.pages[0].url},
        {"id": "f2", "kind": "other", "text": "A blue page.", "quote": "A blue page.", "source_url": crawl.pages[1].url, "evidence": "visual"},
        {"id": "f3", "kind": "claim", "text": "wrong source", "quote": "Made for everyday adventures.", "source_url": crawl.pages[1].url},
        {"id": "f4", "kind": "claim", "text": "invented", "quote": "Award-winning technology", "source_url": crawl.pages[0].url},
    ])
    checked, notes = sources.verify_file_profile(profile, tmp_path)
    assert [fact.id for fact in checked.facts] == ["f1", "f2"]
    assert len(notes) == 3


@pytest.mark.parametrize("supplement", ["", "1\nDiscover the collection.\nDiscover the collection."])
async def test_hybrid_pdf_keeps_native_and_additional_ocr_text(tmp_path, supplement):
    files = store_sources(tmp_path, [("hybrid.pdf", text_pdf("1"))])
    llm = FakeLLM({"source": [{"transcript": supplement, "visual_description": "A brochure page."}]})
    await sources.read_sources(tmp_path, files, llm, lambda _: None)
    page = sources.CrawlResult.load(tmp_path / "crawl").pages[0]
    data = page.data
    assert data["native_text"] == "1" and page.url == "sources/01.pdf#page=1"
    assert json.loads(llm.calls[0][1][1].content[0]["text"])["native_text"] == "1"
    assert "never infer missing text" in llm.calls[0][1][0].content
    if supplement:
        assert data["source_text"] == "1\n\nDiscover the collection."
        assert data["ocr_text"] == "Discover the collection."
        assert data["text_method"] == "pdf+ocr"
        assert "Discover the collection." in sources.file_profile_prompt(tmp_path)
    else:
        assert data["source_text"] == "1" and data["ocr_text"] == ""
        assert data["text_method"] == "pdf"


@pytest.mark.parametrize("files,asset", [
    ([("photo.png", image_bytes())], "img/source-01.png"),
    ([("brochure.pdf", text_pdf()), ("photo.jpg", image_bytes("JPEG"))], "img/source-01-page-01.png"),
    ([("illustrated.pdf", illustrated_pdf())], "img/source-01-page-01-image-01.png"),
])
async def test_files_pipeline_reaches_human_review_without_website_or_five_facts(tmp_path, monkeypatch, files, asset):
    monkeypatch.setattr(runtime, "workspace_dir", lambda: tmp_path)

    async def no_crawl(*args, **kwargs):
        raise AssertionError("File jobs must not crawl a website")

    async def board(path):
        assert (path / "brand.json").is_file()

    async def checks(site):
        from types import SimpleNamespace
        assert (site / "assets" / "brand" / "img").is_dir()
        return SimpleNamespace(as_dict=lambda: {"ok": True, "issues": [], "texts": []})

    async def keyframes(state, config):
        return {"keyframes": []}

    monkeypatch.setattr(graph, "crawl", no_crawl)
    monkeypatch.setattr(graph, "render_board", board)
    monkeypatch.setattr(graph, "check_site", checks)
    monkeypatch.setattr(graph, "n_keyframes", keyframes)
    concept = {"title": "A visual story", "language": "en", "rationale": "Let the imagery tell the story.",
               "hook": "The original image", "palette": {"dark": "#111827", "light": "#ffffff"},
               "scenes": [{"id": f"s-{i}", "start": i * 5, "duration": 5, "purpose": "show the image",
                           "visual": "A gentle pan", "assets": [asset], "texts": []} for i in range(3)]}
    llm = FakeLLM({"source": [{"transcript": "", "visual_description": "An orange image."}],
                  "source_image": [{"visual_description": "An isolated orange motif."}],
                  "distill": [{"company_name": "", "language": "en", "one_liner": "", "audience": "", "tone": "calm", "facts": []}],
                  "concept": [concept], "qa": [{"approved": True, "summary": "Ready for review", "issues": []}],
                  "compose": [f'<file name="composition.html"><section class="scene" id="s-0"><img src="assets/brand/{asset}" alt=""></section><section class="scene" id="s-1"></section><section class="scene" id="s-2"></section></file>'
                              '<file name="composition.css">.scene {position:absolute;inset:0}</file>'
                              '<file name="composition.js">MG.composition(() => {});</file>']})
    job = runtime.create_job("", 15, source_files=files)
    state = await runtime.run_job(job, llm=llm)
    assert not state.get("error") and "__interrupt__" in state
    assert state["source_mode"] == "files" and state["source_files"]
    assert state["check"]["ok"]
    assert json.loads((tmp_path / "jobs" / job / "status.json").read_text())["status"] == "review"
    assert (tmp_path / "jobs" / job / "site" / "assets" / "brand" / asset).is_file()
    saved = await runtime.job_state(job)
    assert saved["next"] == ["review"]
    assert "Never infer" in next(msgs for step, msgs in llm.calls if step == "distill")[0].content
    brief = next(msgs for step, msgs in llm.calls if step == "concept")[1].content
    assert '"website": ""' in brief and "An orange image." in brief
    if "-image-" in asset:
        assert asset in brief and "An isolated orange motif." in brief
        compose = next(msgs for step, msgs in llm.calls if step == "compose")[1].content
        assert "assets/brand/" + asset in compose
        crawl = sources.CrawlResult.load(tmp_path / "jobs" / job / "crawl")
        assert len(crawl.pages) == 1 and crawl.pages[0].url == "sources/01.pdf#page=1"
        assert "Made for everyday adventures." in crawl.pages[0].data["native_text"]


def test_pdf_pages_require_confirmation_without_a_hard_limit(tmp_path):
    assert sources.validate_sources([("guide.pdf", pdf_bytes(20))])[0][0]["pages"] == 20
    files = [("guide.pdf", pdf_bytes(11)), ("appendix.pdf", pdf_bytes(10))]
    with pytest.raises(sources.PDFConfirmationRequired) as pending:
        sources.validate_sources(files)
    assert pending.value.pages == 21
    validated = sources.validate_sources(files, confirm_pdf_pages=True)
    for meta, content in validated:
        path = tmp_path / meta["file"]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    prepared = sources.prepare_sources(tmp_path, [meta for meta, _ in validated])
    assert len(prepared) == 21
    assert prepared[-1]["source"] == "sources/02.pdf#page=10"
    assert sources.validate_sources([("long.pdf", pdf_bytes(101))], confirm_pdf_pages=True)[0][0]["pages"] == 101


@pytest.mark.parametrize("count", [1, 10])
def test_total_file_size_allows_100_mb(tmp_path, count):
    assert sources.MAX_TOTAL_BYTES == 100 * 1024 * 1024
    content = pdf_bytes().ljust(sources.MAX_TOTAL_BYTES // count, b" ")
    validated = sources.validate_sources([(f"{n}.pdf", content) for n in range(count)])
    assert sum(len(data) for _, data in validated) == sources.MAX_TOTAL_BYTES

    if count == 1:
        meta, data = validated[0]
        path = tmp_path / meta['file']
        path.parent.mkdir()
        path.write_bytes(data)
        assert len(sources.prepare_sources(tmp_path, [meta])) == 1
    with pytest.raises(ValueError, match='100 MB'):
        sources.validate_sources([('too-large.pdf', content + b'x')] +
                                 [(f'{n}.pdf', content) for n in range(count - 1)])
