"""Embedded PDF image extraction, using real PDFium image objects and masks."""

import io
import json

import pypdfium2 as pdfium
from PIL import Image

from app.pipeline import sources
from app.pipeline.llm import FakeLLM


def image_pdf(image=None, pages=1):
    image = image if image is not None else Image.new("RGB", (120, 80), "red")
    output = io.BytesIO()
    with sources.PDF_LOCK, pdfium.PdfDocument.new() as pdf:
        for _ in range(pages):
            page = pdf.new_page(600, 800)
            bitmap = pdfium.PdfBitmap.from_pil(image)
            try:
                obj = pdfium.PdfImage.new(pdf)
                obj.set_bitmap(bitmap)
                obj.set_matrix(pdfium.PdfMatrix(240, 0, 0, 160, 40, 300))
                page.insert_obj(obj)
                page.gen_content()
            finally:
                bitmap.close()
                page.close()
        pdf.save(output)
    return output.getvalue()


def prepare_pdf(root, content):
    [(metadata, data)] = sources.validate_sources([("illustrated.pdf", content)])
    path = root / metadata["file"]
    path.parent.mkdir()
    path.write_bytes(data)
    return sources.prepare_sources(root, [metadata])


def test_embedded_image_is_separate_from_its_page_and_keeps_alpha(tmp_path):
    with Image.new("RGBA", (120, 80), (0, 0, 0, 0)) as image:
        image.paste((220, 40, 40, 128), (20, 20, 100, 60))
        [page] = prepare_pdf(tmp_path, image_pdf(image))
    assert page["source"] == "sources/01.pdf#page=1"
    assert page["extraction_warnings"] == []
    [asset] = page["embedded_images"]
    assert asset["file"] != page["file"]
    with Image.open(tmp_path / "brand" / asset["file"]) as extracted:
        assert extracted.size == (120, 80)
        assert extracted.mode == "RGBA"
        assert extracted.getpixel((0, 0))[3] == 0
        red, green, blue, alpha = extracted.getpixel((60, 40))
        assert red > 200 and green < 50 and blue < 50
        assert 120 <= alpha <= 136
    with Image.open(tmp_path / "brand" / page["file"]) as full_page:
        assert full_page.size == (1200, 1600)


def test_duplicate_embedded_images_share_one_file_across_pages(tmp_path):
    pages = prepare_pdf(tmp_path, image_pdf(pages=2))
    assert pages[0]["embedded_images"] == pages[1]["embedded_images"]
    assert len(pages[0]["embedded_images"]) == 1
    assert pages[0]["file"] != pages[1]["file"]
    assert len(list((tmp_path / "brand" / "img").glob("*-image-*.png"))) == 1


def test_large_embedded_image_is_bounded_for_video_use(tmp_path):
    with Image.new("RGB", (3000, 1500), "blue") as image:
        [page] = prepare_pdf(tmp_path, image_pdf(image))
    [asset] = page["embedded_images"]
    with Image.open(tmp_path / "brand" / asset["file"]) as extracted:
        assert max(extracted.size) == 2048
        assert extracted.size == (asset["width"], asset["height"])
        assert extracted.getpixel((100, 100))[:3] == (0, 0, 255)


def test_image_decode_failure_keeps_full_page_and_reports_warning(tmp_path, monkeypatch):
    content = image_pdf()

    def fail(*args, **kwargs):
        raise pdfium.PdfiumError("unsupported embedded image")

    monkeypatch.setattr(pdfium.PdfImage, "get_bitmap", fail)
    [page] = prepare_pdf(tmp_path, content)
    assert page["embedded_images"] == []
    assert len(page["extraction_warnings"]) == 1
    assert "unsupported embedded image" in page["extraction_warnings"][0]
    with Image.open(tmp_path / "brand" / page["file"]) as full_page:
        assert full_page.size == (1200, 1600)
        assert full_page.getpixel((100, 700)) == (255, 0, 0)


async def test_repeated_pdf_image_is_described_once_and_registered_with_page_references(tmp_path):
    [(metadata, content)] = sources.validate_sources([("illustrated.pdf", image_pdf(pages=2))])
    path = tmp_path / metadata["file"]
    path.parent.mkdir()
    path.write_bytes(content)
    llm = FakeLLM({
        "source": [{"transcript": "", "visual_description": "A white page with a red inset image."}],
        "source_image": [{"visual_description": "A solid red rectangle."}],
    })
    await sources.read_sources(tmp_path, [metadata], llm, lambda _: None)
    pages = sources.CrawlResult.load(tmp_path / "crawl").pages
    expected_sources = ["sources/01.pdf#page=1", "sources/01.pdf#page=2"]
    assert [page.url for page in pages] == expected_sources
    assert [item["source"] for item in json.loads(sources.file_profile_prompt(tmp_path))] == expected_sources
    assert [step for step, _ in llm.calls].count("source") == 2
    assert [step for step, _ in llm.calls].count("source_image") == 1
    assert pages[0].data["embedded_images"] == pages[1].data["embedded_images"]
    [asset] = pages[0].data["embedded_images"]
    assert asset["visual_description"] == "A solid red rectangle."

    sources.file_brand(tmp_path)
    images = json.loads((tmp_path / "brand" / "brand.json").read_text())["images"]
    assert len(images) == 3
    [extracted] = [image for image in images if image["file"] == asset["file"]]
    assert extracted["alt"] == "A solid red rectangle."
    assert extracted["page"] == expected_sources[0]
    assert [image["page"] for image in images if image["file"] != asset["file"]] == expected_sources
    assert all((tmp_path / "brand" / image["file"]).is_file() for image in images)
