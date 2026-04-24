# Categorization batch A

Sites in this batch: 34. Research date: 2026-04-24. opencli: 1.7.7.

## antigravity

**Reads:** `dump`, `extract-code`, `read`, `status`, `watch`
**Writes:** `serve`, `model`, `new`, `send`
**Unsure:** `serve` — starts an API proxy; not a state-modifying write per se but runs a persistent server, treated as WRITE (side-effectful)

## 1688

**Reads:** `assets`, `download`, `item`, `search`, `store`
**Writes:**

## 36kr

**Reads:** `article`, `hot`, `news`, `search`
**Writes:**

## 51job

**Reads:** `company`, `detail`, `hot`, `search`
**Writes:**

## amazon

**Reads:** `bestsellers`, `discussion`, `movers-shakers`, `new-releases`, `offer`, `product`, `search`
**Writes:**

## apple-podcasts

**Reads:** `episodes`, `search`, `top`
**Writes:**

## arxiv

**Reads:** `paper`, `search`
**Writes:**

## baidu-scholar

**Reads:** `search`
**Writes:**

## band

**Reads:** `bands`, `mentions`, `post`, `posts`
**Writes:**

## barchart

**Reads:** `flow`, `greeks`, `options`, `quote`
**Writes:**

## bbc

**Reads:** `news`
**Writes:**

## bilibili

**Reads:** `comments`, `download`, `dynamic`, `favorite`, `feed`, `feed-detail`, `following`, `history`, `hot`, `me`, `ranking`, `search`, `subtitle`, `user-videos`, `video`
**Writes:**

## binance

**Reads:** `asks`, `depth`, `gainers`, `klines`, `losers`, `pairs`, `price`, `prices`, `ticker`, `top`, `trades`
**Writes:**

## bloomberg

**Reads:** `businessweek`, `economics`, `feeds`, `industries`, `main`, `markets`, `news`, `opinions`, `politics`, `tech`
**Writes:**

## bluesky

**Reads:** `feeds`, `followers`, `following`, `profile`, `search`, `starter-packs`, `thread`, `trending`, `user`
**Writes:**

## boss

**Reads:** `chatlist`, `chatmsg`, `detail`, `joblist`, `recommend`, `resume`, `search`, `stats`
**Writes:** `batchgreet`, `exchange`, `greet`, `invite`, `mark`, `send`

## chaoxing

**Reads:** `assignments`, `exams`
**Writes:**

## chatgpt

**Reads:** `image`
**Writes:**
**Unsure:** `image` — generates image via ChatGPT web and saves locally; consumes user's quota on the remote account. Conservative classification: WRITE (affects remote usage / billing). Flagging as UNSURE.

## chatgpt-app

**Reads:** `read`, `status`
**Writes:** `ask`, `model`, `new`, `send`

## chatwise

**Reads:** `export`, `history`, `read`
**Writes:** `ask`, `model`, `send`

## cnki

**Reads:** `search`
**Writes:**

## codex

**Reads:** `export`, `extract-diff`, `history`, `read`
**Writes:** `ask`, `model`, `send`

## coupang

**Reads:** `search`
**Writes:** `add-to-cart`

## ctrip

**Reads:** `search`
**Writes:**

## cursor

**Reads:** `export`, `extract-code`, `history`, `read`
**Writes:** `ask`, `composer`, `model`, `send`

## deepseek

**Reads:** `history`, `read`, `status`
**Writes:** `ask`, `new`, `send`

## devto

**Reads:** `tag`, `top`, `user`
**Writes:**

## dictionary

**Reads:** `examples`, `search`, `synonyms`
**Writes:**

## discord-app

**Reads:** `channels`, `members`, `read`, `search`, `servers`, `status`
**Writes:** `delete`, `send`

## douban

**Reads:** `book-hot`, `download`, `marks`, `movie-hot`, `photos`, `reviews`, `search`, `subject`, `top250`
**Writes:**

## doubao

**Reads:** `detail`, `history`, `meeting-summary`, `meeting-transcript`, `read`, `status`
**Writes:** `ask`, `new`, `send`

## doubao-app

**Reads:** `dump`, `read`, `screenshot`, `status`
**Writes:** `ask`, `new`, `send`

## douyin

**Reads:** `activities`, `collections`, `drafts`, `hashtag`, `location`, `profile`, `stats`, `user-videos`, `videos`
**Writes:** `delete`, `draft`, `publish`, `update`

## eastmoney

**Reads:** `announcement`, `convertible`, `etf`, `holders`, `hot-rank`, `index-board`, `kline`, `kuaixun`, `longhu`, `money-flow`, `northbound`, `quote`, `rank`, `sectors`
**Writes:**
