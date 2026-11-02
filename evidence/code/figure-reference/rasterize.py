"""Rasterize the exact exported SVG, without browser chrome or external requests."""
import pathlib,sys
from playwright.sync_api import sync_playwright
svg=pathlib.Path(sys.argv[1]);output=pathlib.Path(sys.argv[2]);scale=int(sys.argv[3]);tmp=output.with_suffix('.tmp.png')
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--disable-dev-shm-usage']);page=b.new_page(viewport={'width':1200,'height':1100},device_scale_factor=scale)
 page.route('**/*',lambda route:route.abort());page.set_content('<html><body style="margin:0">'+svg.read_text()+'</body></html>',wait_until='load');page.locator('svg').screenshot(path=str(tmp));b.close()
tmp.replace(output)
