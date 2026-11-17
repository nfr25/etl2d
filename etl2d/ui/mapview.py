"""The Map panel: a kept file drawn by render2d, navigated as carto2D's map (notes §9).

During a drag or a wheel, the last image is moved and stretched; when the
gesture is over (a quarter of a second without one), render2d draws the view
again, in a thread. The view: a centre and a resolution (map units per pixel).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from imgui_bundle import hello_imgui, imgui

from etl2d.keep import file_view
from etl2d.ui.settings import Settings
from etl2d.ui.state import State

GREY = imgui.ImVec4(0.62, 0.62, 0.62, 1.0)
RED = imgui.ImVec4(1.0, 0.45, 0.40, 1.0)
QUIET = 0.25        # seconds without a gesture before drawing again
METRES_PER_PIXEL = 0.0254 / 96   # a screen pixel, to say the scale as 1:5,000


class MapView:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path: Path | None = None
        self.mtime = 0.0
        self.crs: str | None = None
        self.extent = (0.0, 0.0, 1.0, 1.0)
        self.center = (0.0, 0.0)
        self.res = 1.0                 # map units per pixel
        self.size = (0, 0)             # the panel, in pixels
        self.texture = None            # hello_imgui.TextureGpu of the last image
        self.image_bbox = None         # the area that image shows
        self.need = False              # the view changed since the last image asked
        self.gesture = 0.0             # time of the last drag or wheel
        self.busy = False
        self.done = None               # (png bytes, bbox) from the thread, uploaded by the GUI thread
        self.error = ""
        self.seconds = 0.0

    # ---- the view ----------------------------------------------------------------------------
    def fit(self) -> None:
        """The whole file in view, plus 5 %; a single point: 100 map units around it."""
        x0, y0, x1, y1 = self.extent
        w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        if w < 1e-6 and h < 1e-6:
            w = h = 100.0
        self.center = ((x0 + x1) / 2, (y0 + y1) / 2)
        pw, ph = max(self.size[0], 1), max(self.size[1], 1)
        self.res = max(w / pw, h / ph) * 1.05
        self.need = True

    def bbox(self):
        hw, hh = self.size[0] * self.res / 2, self.size[1] * self.res / 2
        return (self.center[0] - hw, self.center[1] - hh, self.center[0] + hw, self.center[1] + hh)

    def follow(self, path: Path) -> None:
        """Another file: its view; the same file written again (a new run): drawn again, the view kept."""
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if path != self.path:
            self.path, self.mtime = path, mtime
            self.texture, self.image_bbox, self.error = None, None, ""
            if mtime:
                try:
                    self.crs, self.extent = file_view(path)
                except RuntimeError as e:
                    self.error = str(e)
                self.fit()
        elif mtime != self.mtime:
            self.mtime = mtime
            self.need = True

    # ---- render2d, in a thread ---------------------------------------------------------------
    def start(self) -> None:
        exe = self.settings.render2d_exe()
        if exe is None:
            self.error = "render2d not found: Window > Settings"
            self.need = False
            return
        bbox, (w, h) = self.bbox(), self.size
        scale = imgui.get_io().display_framebuffer_scale.x or 1.0   # a screen at 150 %: 1.5 times the pixels
        args = [exe]
        if self.settings.basemap:
            args.append(self.settings.basemap)
        args.append(str(self.path))
        if self.crs:
            args += ["--crs", self.crs]
        out = Path(tempfile.gettempdir()) / f"etl2d-map-{os.getpid()}.png"
        args += ["--bbox", *(f"{v:.3f}" for v in bbox), "--size", f"{round(w * scale)}x{round(h * scale)}",
                 "--pixel-ratio", f"{scale:g}", "-o", str(out)]
        self.need, self.busy = False, True

        def work():
            t0 = time.perf_counter()
            try:
                r = subprocess.run(args, capture_output=True, text=True, timeout=120,
                                   creationflags=0x08000000 if os.name == "nt" else 0)   # no console flashing
                if r.returncode != 0:
                    self.error = (r.stderr or r.stdout or f"render2d: exit {r.returncode}").strip().splitlines()[-1]
                else:
                    self.done = (out.read_bytes(), bbox)
                    self.error = ""
            except (OSError, subprocess.SubprocessError) as e:
                self.error = f"render2d: {e}"
            finally:
                out.unlink(missing_ok=True)
                self.seconds = time.perf_counter() - t0
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    # ---- the panel -----------------------------------------------------------------------------
    def draw(self, state: State) -> None:
        g = state.graph
        if state.looked is None or state.looked not in g.keep:
            imgui.text_colored(GREY, "nothing kept: a right click on an output (or a link) > Keep, then F5")
            return
        nid, port = state.looked
        path = g.kept_path(nid, port)
        self.follow(path)
        imgui.text(f"#{nid} {g.nodes[nid].type}: {port}" if nid in g.nodes else port)
        imgui.same_line()
        if imgui.small_button("whole"):
            self.fit()
        if not self.mtime:
            imgui.same_line()
            imgui.text_colored(GREY, "not written yet: F5 runs the workflow")
            return

        # The image of the last drawing, now in a texture (the GUI thread only may make one).
        if self.done is not None:
            data, bbox = self.done
            self.done = None
            try:
                rgba = hello_imgui.load_image_data_from_encoded_data(data, 4)
                self.texture = hello_imgui.create_texture_gpu_from_rgba_data(rgba)
                self.image_bbox = bbox
            except Exception as e:   # an image render2d wrote that cannot be read
                self.error = f"image: {e}"

        p0 = imgui.get_cursor_screen_pos()
        avail = imgui.get_content_region_avail()
        w, h = int(avail.x), int(avail.y - imgui.get_text_line_height_with_spacing())
        if w < 20 or h < 20:
            return
        if (w, h) != self.size:
            first = self.size == (0, 0)
            self.size = (w, h)
            if first:
                self.fit()
            self.need = True
        imgui.invisible_button("map", imgui.ImVec2(w, h))
        io = imgui.get_io()
        now = time.monotonic()
        if imgui.is_item_active() and (io.mouse_delta.x or io.mouse_delta.y):   # a drag: the map follows
            self.center = (self.center[0] - io.mouse_delta.x * self.res, self.center[1] + io.mouse_delta.y * self.res)
            self.gesture, self.need = now, True
        mx, my = io.mouse_pos.x - p0.x, io.mouse_pos.y - p0.y
        wx = self.center[0] + (mx - w / 2) * self.res          # the mouse, in the map
        wy = self.center[1] - (my - h / 2) * self.res
        if imgui.is_item_hovered() and io.mouse_wheel:          # a wheel: the point under the mouse stays put
            self.res *= 0.8 ** io.mouse_wheel
            self.center = (wx - (mx - w / 2) * self.res, wy + (my - h / 2) * self.res)
            self.gesture, self.need = now, True
        if imgui.is_item_hovered() and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
            self.fit()
        if self.need and not self.busy and not imgui.is_item_active() and now - self.gesture > QUIET:
            self.start()

        dl = imgui.get_window_draw_list()
        dl.push_clip_rect(p0, imgui.ImVec2(p0.x + w, p0.y + h), True)
        dl.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + h), imgui.IM_COL32(255, 255, 255, 255))
        if self.texture is not None and self.image_bbox is not None:
            bx0, by0, bx1, by1 = self.image_bbox
            def screen(x, y):
                return imgui.ImVec2(p0.x + w / 2 + (x - self.center[0]) / self.res,
                                    p0.y + h / 2 - (y - self.center[1]) / self.res)
            dl.add_image(imgui.ImTextureRef(self.texture.texture_id()), screen(bx0, by1), screen(bx1, by0))
        dl.pop_clip_rect()

        # Under the map: the scale, the mouse in the map's system, what render2d said.
        scale = self.res / METRES_PER_PIXEL
        line = f"1:{scale:,.0f}"
        if imgui.is_item_hovered():
            line += f"   {wx:,.1f}  {wy:,.1f}"
        if self.crs:
            line += f"   {self.crs}"
        if self.busy:
            line += "   drawing..."
        elif self.seconds:
            line += f"   drawn in {self.seconds:.2f} s"
        imgui.text_colored(GREY, line)
        if self.error:
            imgui.same_line()
            imgui.text_colored(RED, self.error)
