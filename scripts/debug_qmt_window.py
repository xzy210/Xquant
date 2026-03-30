from __future__ import annotations

from pywinauto import Desktop


HINTS = ("中金财富", "QMT", "极速策略", "登录")


def matches(title: str) -> bool:
    title = (title or "").strip().lower()
    return any(h.lower() in title for h in HINTS)


def main() -> None:
    for backend in ("uia", "win32"):
        print(f"===== BACKEND: {backend} =====")
        try:
            windows = Desktop(backend=backend).windows()
        except Exception as exc:
            print(f"LIST WINDOWS FAILED: {exc}")
            continue

        print("TOP WINDOWS SNAPSHOT:")
        for idx, window in enumerate(windows[:80], start=1):
            try:
                title = (window.window_text() or "").strip()
                handle = getattr(window, "handle", None)
                try:
                    cls_name = window.friendly_class_name()
                except Exception:
                    cls_name = "unknown"
                print(f"  [{idx}] title={title!r} handle={handle} class={cls_name}")
            except Exception:
                continue

        matched = []
        for window in windows:
            try:
                title = (window.window_text() or "").strip()
                if matches(title):
                    matched.append(window)
            except Exception:
                continue

        print(f"MATCHED WINDOWS: {len(matched)}")
        for idx, window in enumerate(matched, start=1):
            try:
                title = (window.window_text() or "").strip()
                handle = getattr(window, "handle", None)
                try:
                    cls_name = window.friendly_class_name()
                except Exception:
                    cls_name = "unknown"
                print(f"--- WINDOW {idx}: title={title!r} handle={handle} class={cls_name} ---")
                try:
                    descendants = window.descendants()
                    print(f"DESCENDANT COUNT: {len(descendants)}")
                    for child_idx, child in enumerate(descendants[:300], start=1):
                        try:
                            text = (child.window_text() or "").strip()
                        except Exception:
                            text = ""
                        try:
                            info = child.element_info
                            control_type = getattr(info, "control_type", "") or ""
                            class_name = getattr(info, "class_name", "") or ""
                            automation_id = getattr(info, "automation_id", "") or ""
                            name = getattr(info, "name", "") or ""
                        except Exception:
                            control_type = ""
                            class_name = ""
                            automation_id = ""
                            name = ""
                        print(
                            f"  [{child_idx}] text={text!r} name={name!r} "
                            f"type={control_type!r} class={class_name!r} automation_id={automation_id!r}"
                        )
                except Exception as exc:
                    print(f"DESCENDANTS FAILED: {exc}")
            except Exception as exc:
                print(f"WINDOW INSPECT FAILED: {exc}")


if __name__ == "__main__":
    main()
