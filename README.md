# Soor Ploom Drop Checkout Bot

专门给 [Soor Ploom](https://soorploomclothier.com) 上新抢购用的小工具。到了开售时间，它会把你 wishlist 里的商品（或你在配置里写死的商品）加进购物车，然后用你在 Shop 账号里存好的地址和信用卡完成付款。

它不碰你的账号密码，也不存信用卡信息。做法是连上一个**你自己已经登录好的 Chrome**（通过 Chrome DevTools Protocol，简称 CDP），在里面替你操作。

## 工作流程

1. 等到 `DROP_TIME`。
2. 清空购物车。
3. **主路径**：打开 [wishlist 页](https://soorploomclothier.com/pages/wishlist)，读取 Checkout 按钮里的链接（`/cart/<variant_id>:1,...`），进入结账页。
4. **fallback**：wishlist 读不到、商品不全，或被跳回首页（还没开售时会这样）时，就按 `ITEMS` 逐件调用 `/cart/add.js` 重试加购，然后打开 `/checkout`。
   - 每秒重试一次，最多 `ADD_WINDOW_S` 秒。
   - 过了 `ADD_ALL_WAIT_S` 秒还有没加上的，先结账已经加上的。
   - 遇到限流（HTTP 429）会刷新页面、放慢重试。
5. 进入 Shop Pay 结账页后，等金额（含运费）稳定，点 **Pay now**，再等最多 90 秒看到订单确认页。

## Soor Ploom 网站的几个特点

这些是实测得到的，脚本就是按这些写的：

- **上新前商品看起来能买，其实不能**：带 `coming-soon` 标签的新品，在商品数据里显示有货，但加购物车会返回 422 "Some items in your cart cannot be purchased at this time"。所以脚本到点后要不停重试，直到加上为止。
- **wishlist 的 Checkout 按钮链接是慢一步出来的**：wishlist 用的是 Swym 插件，Checkout 按钮（`#swymcs-wishlist-checkout`）先显示出来，链接要等大约 1 秒才填上。
- **还没开售时点 Checkout 会被跳回首页**：所以脚本发现被跳回首页，就转去 fallback。
- **结账走 Shop Pay**（`shop.app`）：已登录 Shop 时，地址和卡会自动填好，付款按钮是 `#checkout-pay-button`。
- **开售时间看首页公告**：比如 "Launching October 29th 10:00EDT"，也就是加州时间早上 7:00。

## 前置条件

- **macOS 或 Linux**，装有 Google Chrome。
- **Python 3.9+**（用到 `zoneinfo`）。
- **Playwright**：只用它连接已有的 Chrome，不需要下载它自带的浏览器。

  ```bash
  pip3 install -r requirements.txt
  ```

- **一个 Soor Ploom 账号**，要抢的商品已经点 ♡ 加进 wishlist，并选好了花色和尺码。
- **Shop 账号里存好收货地址和默认信用卡**，这样结账页会自动填好，一键就能付款。

## 使用方法

### 1. 启动带 CDP 的 Chrome

在系统自带的终端里运行。必须用单独的配置目录，Chrome 在默认配置目录上不允许开调试端口：

```bash
# macOS
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/.soorploom_bot/chrome_profile" \
  https://soorploomclothier.com/account/login

# Linux
google-chrome --remote-debugging-port=9333 \
  --user-data-dir="$HOME/.soorploom_bot/chrome_profile" \
  https://soorploomclothier.com/account/login
```

注意：
- 不要从 IDE 或 agent 的沙箱终端启动 Chrome，Chrome 的安全沙箱会起不来，网页一打开就崩（"Aw, Snap! Error code 6"）。
- 同一个 `--user-data-dir` 已经有 Chrome 在跑时，再运行上面的命令只会在旧 Chrome 里多开一个窗口，不会重新启动。要先把旧的 Chrome 完全退出。

### 2. 登录

在这个 Chrome 窗口里：
1. 登录 Soor Ploom 账号，打开 wishlist，确认商品、花色、尺码都对。
2. 随便加一件现在在售的商品去结账，确认 Shop Pay 已登录、地址和卡已自动填好，然后清空购物车。

到开售前，这个 Chrome 和开它的终端都要一直开着。

### 3. 修改配置

打开 `checkout_bot.py`，改顶部的"配置"部分：

| 配置项 | 说明 |
|---|---|
| `CDP` | 和启动 Chrome 时的 `--remote-debugging-port` 一致，默认 9333 |
| `DROP_TIME` | 开售时间，注意时区，比如 `datetime(2026, 10, 29, 7, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))` |
| `ITEMS` | 要抢的商品，fallback 时用，也用来核对 wishlist 里的商品是否齐全。建议和 wishlist 保持一致 |
| `TEST_ITEMS` | 一件现在就能买的商品，`--test` 时用 |

`ITEMS` 里每件商品的格式：

```python
{"name": "Hawk Pullover", "variant": "Pigeon / 6", "id": 1234567890123,
 "url": f"{BASE}/products/hawk-pullover-aw26?variant=1234567890123"},
```

- `id`：**variant ID**。每个花色加尺码的组合对应一个 variant ID，就是商品链接里 `?variant=` 后面的数字。
- `name`：商品标题，用来在结账页核对商品。
- `variant`、`url`：只用来打印日志、方便你核对，不影响下单。

**怎么查 variant ID：**
- 在商品页选好花色和尺码，看地址栏里的 `?variant=`。
- 或者打开 `https://soorploomclothier.com/products/<商品 handle>.js`，在 `variants` 里按 `title`（比如 `"Pigeon / 6"`）找到对应的 `id`。

### 4. 先测试

```bash
# 用 TEST_ITEMS 走一遍：加购 -> 进入结账页，不付款
python3 checkout_bot.py --test

# 立即用 ITEMS 走一遍，不付款
# 还没开售时会被跳回首页、转去 fallback，然后一直收到 422 重试，看到流程正常后按 Ctrl+C 停掉
python3 checkout_bot.py --dry-run --now
```

测完记得在 Chrome 里清空购物车。不过正式运行开始时脚本也会自动清空。

不要频繁测试，Shopify 会限流（返回 429 "Verifying your connection"），限流可能持续一段时间。开售前一天测一次就够了。

### 5. 正式运行

```bash
python3 checkout_bot.py
```

在开售前一两分钟启动就行，它会自己等到 `DROP_TIME` 才开始抢。也可以交给定时任务触发，比如 cron、launchd 或其他调度工具，但**必须跑在开着 Chrome 的那台机器上**，因为脚本连的是本机的 CDP 端口。

运行期间别让电脑休眠，macOS 上可以这样启动：

```bash
caffeinate -i python3 checkout_bot.py
```

## 命令行参数

| 参数 | 效果 |
|---|---|
| （无参数） | 等到 `DROP_TIME`，抢完**自动付款** |
| `--dry-run` | 照常运行，但停在结账页，不点 Pay now |
| `--now` | 不等 `DROP_TIME`，立即开始 |
| `--test` | 立即用 `TEST_ITEMS` 跑一遍，不点 Pay now |

## 注意事项

- **不带参数运行会真的扣款。** 先用 `--test` 和 `--dry-run` 确认商品、尺码和金额都对。
- **付款可能需要额外验证**：如果出现验证码或银行验证（3DS），脚本等 90 秒后会打印提示，需要你在 Chrome 里手动完成。
- **`chrome_profile` 目录里有你的登录 cookie，绝对不要提交到 git。** `.gitignore` 已经排除了 `chrome_profile*/`。
- **请遵守 Soor Ploom 的服务条款**，以及每人限购数量等规定。
- **页面结构可能会变**：脚本依赖 `#swymcs-wishlist-checkout`、`#checkout-pay-button` 这些选择器，网站或 Shopify 改版后可能需要更新。

## License

MIT
