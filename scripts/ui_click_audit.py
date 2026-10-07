"""Click every button/tab on every page and report console errors, failed requests and error banners.

    python scripts/ui_click_audit.py --base http://127.0.0.1:8000 --user supervisor --password super123 [--pages all|a,b] [--out report.json]

Destructive-looking buttons (delete, purge, sign out, demo switch ...) are skipped.
"""
import argparse, json, re, sys
from playwright.sync_api import sync_playwright

SKIP = re.compile(r"delete|purge|clear|reset|remove|deactivate|disable|sign out|logout|restore|force|revoke|erase|wipe|create user", re.I)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--user", required=True); ap.add_argument("--password", required=True)
    ap.add_argument("--pages", default="all"); ap.add_argument("--case-index", type=int, default=1); ap.add_argument("--out", default="")
    ap.add_argument("--max", type=int, default=30)
    a = ap.parse_args()
    report, problems = {}, 0
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
        cur = {"errs": []}
        pg.on("console", lambda m: cur["errs"].append("console: " + m.text[:160]) if m.type == "error" and "Failed to load resource" not in m.text else None)
        pg.on("pageerror", lambda e: cur["errs"].append("pageerror: " + str(e)[:200]))
        pg.on("response", lambda r: cur["errs"].append(f"HTTP {r.status} {r.request.method} {r.url.split('?')[0].replace(a.base, '')}") if r.status >= 400 and r.status != 401 else None)
        pg.goto(a.base + "/"); pg.fill("#username", a.user); pg.fill("#password", a.password); pg.click("button[type=submit]"); pg.wait_for_timeout(3000)
        try: pg.select_option("#case-select", index=a.case_index); pg.wait_for_timeout(1200)
        except Exception: pass
        keys = [x.get_attribute("data-view") for x in pg.locator("nav.nav > button[data-view]").all()]
        if a.pages != "all": keys = [k for k in keys if k in a.pages.split(",")]
        for k in keys:
            cur["errs"] = []; clicked = []
            pg.click(f'nav.nav > button[data-view="{k}"]'); pg.mouse.move(900, 500); pg.wait_for_timeout(1800)  # move off the icon rail so it collapses
            n = 0
            while n < a.max:
                btns = pg.locator("main button:visible:not([disabled])")
                cnt = btns.count()
                if n >= cnt: break
                bt = btns.nth(n); n += 1
                try: label = (bt.inner_text(timeout=1000) or bt.get_attribute("aria-label") or bt.get_attribute("title") or "").strip()[:40]
                except Exception: continue
                if SKIP.search(label) or SKIP.search(bt.get_attribute("class") or "") : continue
                before = len(cur["errs"])
                try: bt.click(timeout=2500)
                except Exception as e: cur["errs"].append(f"unclickable '{label}': {str(e)[:80]}"); continue
                pg.mouse.move(900, 500); pg.wait_for_timeout(700)
                if pg.locator(".notice.bad").count(): cur["errs"].append(f"error banner after '{label}': {pg.locator('.notice.bad').first.inner_text()[:120]}")
                clicked.append(label)
                pg.keyboard.press("Escape")
                if pg.locator(".modal").count(): pg.evaluate("document.querySelectorAll('.overlay').forEach(e=>e.remove())")
                if not pg.locator(f'nav.nav > button[data-view="{k}"].active').count(): break
            report[k] = {"clicked": clicked, "problems": list(dict.fromkeys(cur["errs"]))}
            problems += len(report[k]["problems"])
            print(f"{k:11s} clicked {len(clicked):2d}  problems {len(report[k]['problems'])}")
            for e in report[k]["problems"]: print("     -", e)
        b.close()
    if a.out: json.dump(report, open(a.out, "w"), indent=1)
    sys.exit(1 if problems else 0)

if __name__ == "__main__":
    main()
