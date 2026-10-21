"""Run the native folder dialog on its own main thread/process."""
import json
import sys


def main():
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(parent=root, title='Choose AAASoft download folder', mustexist=True,
                                       initialdir=sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None)
        print(json.dumps({'path': path or None}))
    except Exception:
        print(json.dumps({'error': 'Folder dialog unavailable'}))
        return 1
    finally:
        if root is not None:
            root.destroy()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
