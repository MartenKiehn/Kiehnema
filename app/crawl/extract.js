/* Evaluated in every crawled page. Returns plain JSON; never mutates the page. */
() => {
  const abs = (u) => { try { return u ? new URL(u, location.href).href : null; } catch { return null; } };
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05;
  };
  const area = (el) => (el.closest('header, nav, [role=banner], [role=navigation]') ? 'header'
    : el.closest('footer, [role=contentinfo]') ? 'footer' : 'main');
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const sy = window.scrollY;

  // ---------- content as markdown (document order) ----------
  const BLOCK = 'h1,h2,h3,h4,h5,h6,p,li,blockquote,figcaption,dt,dd';
  const blocks = [];
  const headings = [];
  for (const el of document.querySelectorAll(BLOCK)) {
    if (el.querySelector(BLOCK)) continue; // the inner blocks carry the text
    const text = clean(el.innerText);
    if (!text || text.length < 2 || !vis(el)) continue;
    const tag = el.tagName.toLowerCase();
    const where = area(el);
    if (tag[0] === 'h') {
      const level = Number(tag[1]);
      headings.push({ level, text, area: where });
      blocks.push({ area: where, md: `${'#'.repeat(level)} ${text}` });
    } else if (tag === 'li') blocks.push({ area: where, md: `- ${text}` });
    else if (tag === 'blockquote') blocks.push({ area: where, md: `> ${text}` });
    else blocks.push({ area: where, md: text });
  }

  // buttons / call-to-action links
  const ctas = [];
  for (const el of document.querySelectorAll('a, button')) {
    if (!vis(el)) continue;
    const text = clean(el.innerText);
    if (!text || text.split(' ').length > 6) continue;
    const cs = getComputedStyle(el);
    const filled = cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent';
    const bordered = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none';
    if (!(filled || bordered) || parseFloat(cs.paddingLeft) < 8) continue;
    const r = el.getBoundingClientRect();
    ctas.push({ text, href: abs(el.getAttribute('href')), area: area(el), bg: cs.backgroundColor, color: cs.color,
      radius: cs.borderTopLeftRadius, font: cs.fontFamily, w: r.width, h: r.height });
  }

  const links = [...document.querySelectorAll('a[href]')].map((a) => ({
    href: abs(a.getAttribute('href')), text: clean(a.innerText).slice(0, 80), area: area(a),
  })).filter((l) => l.href);

  // ---------- images / logo candidates ----------
  const nearestHeading = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const h = n.querySelector('h1,h2,h3,h4');
      if (h && clean(h.innerText)) return clean(h.innerText).slice(0, 120);
    }
    return null;
  };
  const describe = (el) => `${el.id || ''} ${typeof el.className === 'string' ? el.className : el.getAttribute('class') || ''}`.trim();
  const images = [...document.querySelectorAll('img')].map((img) => {
    const r = img.getBoundingClientRect();
    return {
      src: abs(img.currentSrc || img.src || img.getAttribute('data-src')), alt: clean(img.alt), w: img.naturalWidth, h: img.naturalHeight,
      rw: r.width, rh: r.height, x: r.left, y: r.top + sy, area: area(img), visible: vis(img),
      link: abs(img.closest('a')?.getAttribute('href')), desc: describe(img), section: nearestHeading(img),
      siblings: img.parentElement?.parentElement?.querySelectorAll('img').length || 0,
    };
  }).filter((i) => i.src && (!i.src.startsWith('data:') || (i.src.startsWith('data:image/') && i.src.length < 600000 && i.rw * i.rh > 400)));
  for (const el of document.querySelectorAll('section, header, div, figure')) {
    const bg = getComputedStyle(el).backgroundImage;
    const m = /url\(["']?([^"')]+)["']?\)/.exec(bg);
    if (!m) continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < 200000) continue;
    images.push({ src: abs(m[1]), alt: '', w: 0, h: 0, rw: r.width, rh: r.height, x: r.left, y: r.top + sy, area: area(el),
      visible: vis(el), link: null, desc: `bg ${describe(el)}`, section: nearestHeading(el), siblings: 0, background: true });
  }
  const svgs = [...document.querySelectorAll('svg')].map((svg) => {
    const r = svg.getBoundingClientRect();
    return { rw: r.width, rh: r.height, x: r.left, y: r.top + sy, area: area(svg), visible: vis(svg),
      link: abs(svg.closest('a')?.getAttribute('href')), desc: `${describe(svg)} ${svg.closest('a')?.getAttribute('aria-label') || ''} ${svg.getAttribute('aria-label') || ''}`.trim(),
      shapes: svg.querySelectorAll('path,circle,rect,polygon,ellipse,line,polyline').length,
      html: svg.outerHTML.length < 300000 ? svg.outerHTML : null };
  }).filter((s) => s.visible && s.rw * s.rh >= 400);

  // text wordmark: the header link to the home page (sites without a logo image set the name as text)
  const homeHref = (h) => { try { const u = new URL(h, location.href); return u.origin === location.origin && (u.pathname === '/' || u.pathname === '') ; } catch { return false; } };
  let wordmark = null;
  for (const a of document.querySelectorAll('header a[href], nav a[href], [role=banner] a[href], a[class*=logo], a[class*=brand]')) {
    const text = clean(a.innerText);
    if (!vis(a) || !text || text.length > 40 || !homeHref(a.getAttribute('href'))) continue;
    const el = [...a.querySelectorAll('*')].reverse().find((n) => clean(n.innerText) === text) || a;
    const cs = getComputedStyle(el);
    wordmark = { text, font: cs.fontFamily, weight: cs.fontWeight, size: cs.fontSize, color: cs.color,
      letterSpacing: cs.letterSpacing, transform: cs.textTransform, hasImage: !!a.querySelector('img, svg') };
    break;
  }

  // ---------- styles ----------
  const style = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { font: cs.fontFamily, weight: cs.fontWeight, size: cs.fontSize, color: cs.color, bg: cs.backgroundColor,
      letterSpacing: cs.letterSpacing, transform: cs.textTransform, lineHeight: cs.lineHeight };
  };
  const firstVisible = (sel) => [...document.querySelectorAll(sel)].find(vis) || null;
  const header = firstVisible('header, [role=banner], nav');
  const hero = [...document.querySelectorAll('main section, body > section, main > div, .hero, [class*=hero]')]
    .find((el) => vis(el) && el.getBoundingClientRect().height > 250) || null;
  const styles = {
    body: style(document.body), h1: style(firstVisible('h1')), h2: style(firstVisible('h2')), h3: style(firstVisible('h3')),
    a: style(firstVisible('main a, a')), header: style(header), hero: style(hero),
    heroBgImage: hero ? getComputedStyle(hero).backgroundImage : null,
  };

  // CSS custom properties + @font-face (same-origin sheets only; cross-origin sheets are listed)
  const cssVars = {};
  const fontFaces = [];
  const foreignSheets = [];
  const rootStyle = getComputedStyle(document.documentElement);
  for (const sheet of document.styleSheets) {
    let rules;
    try { rules = sheet.cssRules; } catch { if (sheet.href) foreignSheets.push(sheet.href); continue; }
    const visit = (list) => {
      for (const rule of list) {
        if (rule.cssRules && !(rule instanceof CSSFontFaceRule)) visit(rule.cssRules);
        if (rule instanceof CSSFontFaceRule) {
          const src = rule.style.getPropertyValue('src');
          const urls = [...src.matchAll(/url\(["']?([^"')]+)["']?\)/g)].map((m) => new URL(m[1], sheet.href || location.href).href);
          fontFaces.push({ family: clean(rule.style.getPropertyValue('font-family')).replace(/^["']|["']$/g, ''),
            weight: rule.style.getPropertyValue('font-weight') || '400', style: rule.style.getPropertyValue('font-style') || 'normal', urls });
        } else if (rule.selectorText && /(^|,)\s*(:root|html)\s*(,|$)/.test(rule.selectorText)) {
          for (const prop of rule.style) {
            if (prop.startsWith('--')) cssVars[prop] = clean(rootStyle.getPropertyValue(prop)) || clean(rule.style.getPropertyValue(prop));
          }
        }
      }
    };
    visit(rules);
  }

  // area-weighted background colours and text-weighted foreground colours
  const bgUse = {};
  const fgUse = {};
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (cs.backgroundColor && !cs.backgroundColor.endsWith(', 0)') && cs.backgroundColor !== 'transparent') {
      bgUse[cs.backgroundColor] = (bgUse[cs.backgroundColor] || 0) + Math.min(r.width * r.height, 4e6);
    }
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join('');
    if (own) fgUse[cs.color] = (fgUse[cs.color] || 0) + own.length * parseFloat(cs.fontSize);
  }
  const bodyBg = getComputedStyle(document.body).backgroundColor;
  const htmlBg = rootStyle.backgroundColor;

  const meta = (sel) => document.querySelector(sel)?.getAttribute('content') || null;
  return {
    url: location.href,
    title: document.title,
    lang: document.documentElement.lang || null,
    description: meta('meta[name=description]'),
    siteName: meta('meta[property="og:site_name"]'),
    ogImage: abs(meta('meta[property="og:image"]')),
    themeColor: meta('meta[name=theme-color]'),
    icons: [...document.querySelectorAll('link[rel~=icon], link[rel=apple-touch-icon]')].map((l) => ({ href: abs(l.getAttribute('href')), sizes: l.getAttribute('sizes'), rel: l.rel })),
    headings, blocks, ctas: ctas.slice(0, 30), links, images, svgs, wordmark, styles, cssVars, fontFaces, foreignSheets,
    loadedFonts: [...document.fonts].filter((f) => f.status === 'loaded').map((f) => ({ family: f.family.replace(/^["']|["']$/g, ''), weight: f.weight, style: f.style })),
    bgUse, fgUse, pageBg: bodyBg && !bodyBg.endsWith(', 0)') ? bodyBg : htmlBg,
    docHeight: document.documentElement.scrollHeight,
  };
}
