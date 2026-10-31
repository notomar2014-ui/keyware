import asyncio, os, sys
from playwright.async_api import async_playwright
BASE = "http://localhost:4173"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "")
async def full(page, path, out):
    await page.goto(BASE + path, wait_until="networkidle")
    h = await page.evaluate("document.body.scrollHeight")
    for y in range(0, h, 500):
        await page.evaluate(f"window.scrollTo(0,{y})"); await page.wait_for_timeout(60)
    await page.evaluate("document.querySelectorAll('.reveal').forEach(e=>e.classList.add('is-in'))")
    if path == "/":
        await page.wait_for_function("document.getElementById('proposal').classList.contains('is-on')", timeout=20000)
    await page.evaluate("window.scrollTo(0,0)"); await page.wait_for_timeout(1200)
    await page.screenshot(path=out, full_page=True, animations="disabled")
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        og = await b.new_page(viewport={"width":1200,"height":630})
        # set_content: the site's CSP (no inline styles) would otherwise apply to the og source page
        html = open(OUT + "tools/og-source.html", encoding="utf-8").read().replace('url(/assets/', 'url(' + BASE + '/assets/').replace('src="/assets/', 'src="' + BASE + '/assets/')
        await og.set_content(html, wait_until="networkidle"); await og.wait_for_timeout(500)
        await og.screenshot(path=OUT + "og.png")
        msgs = []
        for name, vp, mobile in [("desktop", {"width":1440,"height":900}, False), ("mobile", {"width":390,"height":844}, True)]:
            ctx = await b.new_context(viewport=vp, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
            page = await ctx.new_page()
            page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: msgs.append(f"pageerror: {e}"))
            await full(page, "/", OUT + f"preview_{name}.png")
            await full(page, "/guide", OUT + f"preview_guide_{name}.png")
            await ctx.close()
        print("\n".join(msgs) or "no console errors")
        await b.close()
asyncio.run(main())
