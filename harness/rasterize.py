"""Runs INSIDE the bwrap sandbox (see render.py). SVG bytes on stdin -> PNG bytes on stdout, JSON status on stderr.

librsvg has no script engine. The document is loaded from memory with no base URI, so relative and file references
cannot resolve; the sandbox also has no network and no view of the home directory or the data drive. Entity
declarations are refused before librsvg sees the bytes (expat pre-parse), and external entities are never fetched.
"""
import json
import resource
import sys

CANVAS = 512
AS_MIB = 4096
RENDER_VERSION = "v2-as4096"


def status(**kw):
    sys.stderr.write(json.dumps(kw) + "\n")
    sys.stderr.flush()


def main() -> int:
    # 4 GiB address space: 1.5 GiB killed the glycin SVG loader on text + filter documents (sweep-1, render v2)
    resource.setrlimit(resource.RLIMIT_AS, (AS_MIB << 20, AS_MIB << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))
    data = sys.stdin.buffer.read(16 << 20)
    import xml.parsers.expat as E
    p = E.ParserCreate()
    p.SetParamEntityParsing(E.XML_PARAM_ENTITY_PARSING_NEVER)

    def no_entities(*a):
        raise ValueError("entity declaration refused")
    p.EntityDeclHandler = no_entities
    p.ExternalEntityRefHandler = lambda *a: 1          # never resolve
    root = []
    p.StartElementHandler = lambda name, attrs: root.append((name, attrs.get("xmlns"))) if not root else None
    try:
        p.Parse(data, True)
        wellformed, xml_error = True, None
    except (E.ExpatError, ValueError) as e:
        wellformed, xml_error = False, str(e)
    if not wellformed:
        status(ok=False, stage="xml", wellformed=False, xml_error=xml_error)
        return 2
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    # gdk-pixbuf's SVG loader is librsvg. Data in memory, no file name: nothing relative can resolve.
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    intrinsic = {}

    def size_prepared(ld, w, h):                 # fit inside the canvas before anything is rasterized
        intrinsic.update(w=w, h=h)
        if w > 0 and h > 0:
            s = min(CANVAS / w, CANVAS / h)
            ld.set_size(max(1, round(w * s)), max(1, round(h * s)))
    loader.connect("size-prepared", size_prepared)
    try:
        loader.write(data)
        loader.close()
    except GLib.Error as e:
        status(ok=False, stage="rsvg-load", wellformed=True, error=str(e), root=root[:1], intrinsic=intrinsic)
        return 3
    pb = loader.get_pixbuf()
    if pb is None:
        status(ok=False, stage="rsvg-render", wellformed=True, error="no pixbuf", root=root[:1], intrinsic=intrinsic)
        return 4
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, CANVAS, CANVAS)
    canvas.fill(0xFFFFFFFF)
    w, h = pb.get_width(), pb.get_height()
    ox, oy = (CANVAS - w) // 2, (CANVAS - h) // 2
    pb.composite(canvas, ox, oy, w, h, ox, oy, 1.0, 1.0, GdkPixbuf.InterpType.BILINEAR, 255)
    px = canvas.get_pixels()
    stride, nch = canvas.get_rowstride(), canvas.get_n_channels()
    nonwhite = 0
    for y in range(0, CANVAS, 2):                 # every other pixel/row: a blank-render detector, not a metric
        row = y * stride
        for x in range(0, CANVAS, 2):
            i = row + x * nch
            if px[i] < 250 or px[i + 1] < 250 or px[i + 2] < 250:
                nonwhite += 1
    ok, buf = canvas.save_to_bufferv("png", [], [])
    sys.stdout.buffer.write(bytes(buf))
    sys.stdout.flush()
    status(ok=bool(ok), stage="done", wellformed=True, root=root[:1], intrinsic=intrinsic, drawn=[w, h],
           nonwhite_frac=round(nonwhite / (CANVAS * CANVAS / 4), 4), renderer="gdk-pixbuf svg loader (librsvg 2.61.3)", render_version=RENDER_VERSION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
