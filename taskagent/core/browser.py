import random
import time

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.edge.options import Options as EdgeOptions


def attach(debugger_address: str, kind: str = "chrome", window: str = "taskagent"):
    opts = EdgeOptions() if kind == "edge" else Options()
    opts.add_experimental_option("debuggerAddress", debugger_address)
    driver = webdriver.Edge(options=opts) if kind == "edge" else webdriver.Chrome(options=opts)
    for handle in driver.window_handles:
        try:
            driver.switch_to.window(handle)
            if driver.execute_script("return window.name") == window:
                return driver
        except WebDriverException:
            continue
    driver.switch_to.window(driver.window_handles[0])
    driver.switch_to.new_window("tab")
    driver.execute_script("window.name = arguments[0]", window)
    return driver


def ensure_driver(cfg: dict, driver=None):
    if driver is not None:
        try:
            driver.current_window_handle
            return driver
        except WebDriverException:
            print("[browser] lost connection, re-attaching")
    return attach(cfg.get("debugger_address", "127.0.0.1:9222"), cfg.get("kind", "chrome"), cfg.get("window", "taskagent"))


def human_delay(bounds: list[int] | tuple[int, int]):
    time.sleep(random.uniform(*bounds))


def screenshot(driver, folder: str, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
    path = f"{folder}/{safe}_{int(time.time())}.png"
    driver.save_screenshot(path)
    return path
