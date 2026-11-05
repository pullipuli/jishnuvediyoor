#!/usr/bin/env python3
"""Post-build checks for the generated site (standard library only).

Usage: python3 scripts/check-site.py public https://jishnuvediyoor.com/

Fails (exit 1) on problems that would hurt visitors or search engines:
  * robots.txt or sitemap.xml missing, or sitemap URLs that do not exist
  * internal links, images or srcset entries that point at missing files
  * in-page anchors (#id) that point at missing ids
  * pages without a title, meta description, canonical URL or exactly one <h1>
  * duplicate titles or descriptions across indexable pages
  * <img> without an alt attribute, target=_blank links without rel=noopener/noreferrer
  * body elements (nav, div, ...) emitted inside <head>, which pushes the
    rest of the head (Open Graph, JSON-LD) into <body>
Warnings (printed, not fatal): <img> without width/height.
"""
import os
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote

HEAD_OK = {"meta", "link", "title", "style", "script", "noscript", "base", "template"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.head_done = False
        self.head_violations = []
        self.titles = []
        self._in_title = False
        self.meta = {}
        self.canonical = None
        self.h1 = 0
        self.ids = set()
        self.links = []  # (tag, attr, value)
        self.imgs = []
        self.blank_links = []
        self.refresh = False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head, self.head_done = False, True
        elif self.in_head and tag not in HEAD_OK:
            self.head_violations.append(tag)
            self.in_head, self.head_done = False, True
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "title" and not self.head_done:
            self._in_title = True
            self.titles.append("")
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.meta[key.lower()] = a.get("content", "")
            if a.get("http-equiv", "").lower() == "refresh":
                self.refresh = True
        if tag == "link" and "canonical" in a.get("rel", "").split():
            self.canonical = a.get("href")
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and "href" in a:
            self.links.append((tag, "href", a["href"]))
            if a.get("target") == "_blank":
                rel = a.get("rel", "").split()
                if "noopener" not in rel and "noreferrer" not in rel:
                    self.blank_links.append(a["href"])
        if tag == "link" and "href" in a and a.get("rel") in ("stylesheet", "icon", "preload", "apple-touch-icon", "mask-icon"):
            self.links.append((tag, "href", a["href"]))
        if tag in ("img", "script", "source") and a.get("src"):
            self.links.append((tag, "src", a["src"]))
        for key in ("srcset",):
            if a.get(key):
                for part in a[key].split(","):
                    url = part.strip().split(" ")[0]
                    if url:
                        self.links.append((tag, key, url))
        if tag == "img":
            self.imgs.append(a)

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head, self.head_done = False, True
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.titles:
            self.titles[-1] += data


def main():
    root, base = sys.argv[1], sys.argv[2].rstrip("/") + "/"
    base_url = urlparse(base)
    errors, warnings = [], []
    pages = {}

    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.endswith(".html"):
                path = os.path.join(dirpath, f)
                p = Page()
                with open(path, encoding="utf-8") as fh:
                    p.feed(fh.read())
                pages["/" + os.path.relpath(path, root).replace(os.sep, "/")] = p

    def resolve(url, page_path):
        """Return (file_path, fragment) for an internal URL, or None for external."""
        u = urlparse(url)
        if u.scheme in ("mailto", "tel", "data", "javascript"):
            return None
        if u.scheme or u.netloc:
            if u.netloc != base_url.netloc:
                return None
        path = unquote(u.path)
        if not path:
            path = page_path
        elif not path.startswith("/"):
            path = os.path.normpath(os.path.join(os.path.dirname(page_path), path))
            if u.path.endswith("/"):
                path += "/"
        if path.endswith("/"):
            path += "index.html"
        return path, u.fragment

    def exists(path):
        full = os.path.join(root, path.lstrip("/"))
        return os.path.isfile(full) or os.path.isfile(os.path.join(full, "index.html"))

    # robots.txt and sitemap
    robots = os.path.join(root, "robots.txt")
    if not os.path.isfile(robots):
        errors.append("robots.txt is missing")
    else:
        text = open(robots, encoding="utf-8").read()
        if "Sitemap:" not in text:
            errors.append("robots.txt does not reference the sitemap")
        if any(line.strip() == "Disallow: /" for line in text.splitlines()):
            errors.append("robots.txt blocks the whole site (non-production build?)")
    sitemap = os.path.join(root, "sitemap.xml")
    sitemap_paths = set()
    if not os.path.isfile(sitemap):
        errors.append("sitemap.xml is missing")
    else:
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in ET.parse(sitemap).getroot().findall("s:url/s:loc", ns):
            url = loc.text.strip()
            if not url.startswith(base):
                errors.append(f"sitemap URL not on {base}: {url}")
                continue
            target = resolve(url, "/")
            sitemap_paths.add(target[0])
            if not exists(target[0]):
                errors.append(f"sitemap URL has no page: {url}")

    seen_titles, seen_desc = {}, {}
    for path, p in sorted(pages.items()):
        if p.refresh:  # Hugo alias redirect pages
            continue
        if p.head_violations:
            errors.append(f"{path}: <{p.head_violations[0]}> inside <head> (moves later head tags into <body>)")
        title = p.titles[0].strip() if p.titles else ""
        desc = p.meta.get("description", "").strip()
        robots_meta = p.meta.get("robots", "")
        indexable = "noindex" not in robots_meta
        if not title:
            errors.append(f"{path}: missing <title>")
        if not desc:
            errors.append(f"{path}: missing meta description")
        if indexable:
            if not p.canonical:
                errors.append(f"{path}: missing canonical URL")
            elif not p.canonical.startswith(base):
                # Hand-written pages under static/ hard-code the production URL; flag, don't fail.
                cu = urlparse(p.canonical)
                if cu.scheme == "https" and cu.path == path.replace("index.html", ""):
                    warnings.append(f"{path}: canonical uses another host: {p.canonical}")
                else:
                    errors.append(f"{path}: canonical not on {base}: {p.canonical}")
            if p.h1 != 1:
                errors.append(f"{path}: expected one <h1>, found {p.h1}")
            seen_titles.setdefault(title, []).append(path)
            if desc:
                seen_desc.setdefault(desc, []).append(path)
            if path not in sitemap_paths and path != "/404.html":
                warnings.append(f"{path}: indexable but not in sitemap.xml")
        for img in p.imgs:
            if "alt" not in img:
                errors.append(f"{path}: <img src={img.get('src')}> has no alt attribute")
            if not (img.get("width") and img.get("height")) and not img.get("src", "").startswith("data:"):
                if "hidden" not in img:
                    warnings.append(f"{path}: <img src={img.get('src')}> has no width/height")
        for href in p.blank_links:
            errors.append(f"{path}: target=_blank link without rel=noopener: {href}")
        for tag, attr, url in p.links:
            target = resolve(url, path)
            if target is None:
                continue
            file_path, frag = target
            if not exists(file_path):
                errors.append(f"{path}: broken {tag}[{attr}] -> {url}")
                continue
            if frag and tag == "a" and file_path.endswith(".html"):
                key = file_path if file_path in pages else file_path.rstrip("/") + "/index.html"
                other = pages.get(key)
                if other is not None and frag not in other.ids and frag != "top":
                    errors.append(f"{path}: anchor #{frag} not found in {file_path}")

    for title, where in seen_titles.items():
        if len(where) > 1:
            errors.append(f"duplicate title '{title}': {', '.join(where)}")
    for desc, where in seen_desc.items():
        if len(where) > 1:
            errors.append(f"duplicate description '{desc[:60]}…': {', '.join(where)}")

    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    print(f"checked {len(pages)} HTML files: {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
