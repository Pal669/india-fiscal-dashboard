"""One command to refresh everything on the portal.

    python scripts/update_all.py            rebuild budget data from the workbook + pull live data (local only)
    python scripts/update_all.py --push     ...then commit and push, which redeploys the public site
    python scripts/update_all.py --live     live data only (skip the workbook)
    python scripts/update_all.py --xlsx "path\\to\\workbook.xlsx"

The live layer also refreshes by itself every day via GitHub Actions; this script is for "refresh now" and for
picking up an edited workbook (the workbook lives on this PC, so GitHub cannot rebuild the budget layer on its own).
"""
import argparse, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run(*cmd):
    print(">", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="commit and push data changes (redeploys the site)")
    ap.add_argument("--live", action="store_true", help="live data only")
    ap.add_argument("--xlsx", help="path to the fiscal workbook (default: see build_curated.py)")
    a = ap.parse_args()

    run(sys.executable, HERE / "fetch_live.py")  # first: the budget layer converts to US$ at the latest USD/INR
    if not a.live:
        if run(sys.executable, HERE / "build_curated.py", *([a.xlsx] if a.xlsx else [])) != 0:
            sys.exit("Budget rebuild failed; nothing pushed.")

    if a.push:
        subprocess.run(["git", "add", "data"], cwd=ROOT)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
            print("Nothing changed; nothing to push.")
            return
        subprocess.run(["git", "commit", "-m", "Refresh portal data"], cwd=ROOT, check=True)
        subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=ROOT)  # the daily bot may have committed live.json
        if subprocess.run(["git", "push", "origin", "main"], cwd=ROOT).returncode == 0:
            print("Pushed. The site updates within about a minute.")
        else:
            print("Push failed; the commit is saved locally. Run: git push origin main")


if __name__ == "__main__":
    main()
