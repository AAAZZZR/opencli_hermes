"""Security layer: command whitelist, rate limiting, audit log, output sanitization.

Model: deny-list at two layers.

1. `SUPPORTED_SITES` — flat frozenset of site names that fleet-mcp is willing to
   dispatch. Everything opencli offers except framework verbs (browser / adapter /
   daemon / etc.) is here. Sites not in this set are rejected.

2. `FORBIDDEN_GLOBAL` — framework-level verbs (browser, eval, register, install,
   plugin, daemon, adapter, synthesize, record, exec, shell) that must never
   pass even if somehow proposed as a sub-command.

3. `FORBIDDEN_PER_SITE` — per-site write/mutation sub-commands (post, reply,
   comment, like, follow, upvote, publish, subscribe, etc.) that must never run
   on a user's account. Derived from enumerating every `opencli <site> --help`.

Any sub-command NOT in either forbidden set is allowed. Unknown sub-commands
will be rejected by opencli itself downstream; fleet-mcp doesn't maintain the
full per-site sub-command catalogue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fleet_mcp.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported sites — flat allow-list at site level.
# Derived from `opencli --help` output; excludes framework verbs + external
# CLI passthroughs (docker / gh / lark-cli / obsidian / vercel / wecom-cli).
# ---------------------------------------------------------------------------

SUPPORTED_SITES: frozenset[str] = frozenset({
    "1688", "36kr", "51job", "amazon", "antigravity", "apple-podcasts",
    "arxiv", "baidu-scholar", "band", "barchart", "bbc", "bilibili",
    "binance", "bloomberg", "bluesky", "boss", "chaoxing", "chatgpt",
    "chatgpt-app", "chatwise", "cnki", "codex", "coupang", "ctrip",
    "cursor", "deepseek", "devto", "dictionary", "discord-app", "douban",
    "doubao", "doubao-app", "douyin", "eastmoney", "facebook", "gemini",
    "gitee", "google", "google-scholar", "gov-law", "gov-policy", "grok",
    "hackernews", "hf", "hupu", "imdb", "instagram", "jd", "jianyu",
    "jike", "jimeng", "ke", "lesswrong", "linkedin", "linux-do", "lobsters",
    "maimai", "medium", "mubu", "notebooklm", "notion", "nowcoder", "ones",
    "paperreview", "pixiv", "producthunt", "quark", "reddit", "reuters",
    "sinablog", "sinafinance", "smzdm", "spotify", "stackoverflow", "steam",
    "substack", "taobao", "tdx", "ths", "tieba", "tiktok", "twitter",
    "uiverse", "v2ex", "wanfang", "web", "weibo", "weixin", "weread",
    "wikipedia", "xianyu", "xiaoe", "xiaohongshu", "xiaoyuzhou", "xueqiu",
    "yahoo-finance", "yollomi", "youtube", "yuanbao", "zhihu", "zsxq",
})


SITE_DESCRIPTIONS: dict[str, str] = {
    "1688": "1688 — Alibaba B2B marketplace",
    "36kr": "36kr — Chinese tech news",
    "51job": "51job — Chinese recruitment listings",
    "amazon": "Amazon — products, bestsellers, reviews",
    "antigravity": "Antigravity — AI assistant (ask/send/serve blocked)",
    "apple-podcasts": "Apple Podcasts — top charts, episode search",
    "arxiv": "arXiv — papers + search",
    "baidu-scholar": "Baidu Scholar — Chinese academic search",
    "band": "Band — posts, mentions, bands feed",
    "barchart": "Barchart — options + futures data",
    "bbc": "BBC — news",
    "bilibili": "Bilibili — Chinese video platform",
    "binance": "Binance — crypto market data",
    "bloomberg": "Bloomberg — markets, news, politics, tech",
    "bluesky": "Bluesky — decentralized microblog",
    "boss": "BOSS直聘 — recruiter data (batchgreet/greet/send blocked)",
    "chaoxing": "Chaoxing — Chinese online learning",
    "chatgpt": "ChatGPT web — image gen blocked (consumes remote quota)",
    "chatgpt-app": "ChatGPT desktop — read history (ask/send/new blocked)",
    "chatwise": "ChatWise desktop — read history (ask/send blocked)",
    "cnki": "CNKI — Chinese academic search",
    "codex": "OpenAI Codex — read history (ask/send blocked)",
    "coupang": "Coupang — Korean e-commerce (add-to-cart blocked)",
    "ctrip": "Ctrip — travel search",
    "cursor": "Cursor editor — read history (ask/composer/send blocked)",
    "deepseek": "DeepSeek — read history (ask/send blocked)",
    "devto": "dev.to — developer articles",
    "dictionary": "Dictionary — lookup + synonyms",
    "discord-app": "Discord desktop — read channels (send/delete blocked)",
    "douban": "Douban — Chinese book/movie reviews",
    "doubao": "豆包 — read history (ask/send blocked)",
    "doubao-app": "豆包 app — read history (ask/send blocked)",
    "douyin": "Douyin (抖音) — creator tools (publish/draft/delete/update blocked)",
    "eastmoney": "East Money — Chinese stock data",
    "facebook": "Facebook — feed, friends, groups (add-friend/join-group blocked)",
    "gemini": "Google Gemini — read results (ask/new/image blocked)",
    "gitee": "Gitee — Chinese code hosting",
    "google": "Google — search, news, suggest, trends",
    "google-scholar": "Google Scholar — academic papers",
    "gov-law": "Chinese government laws — search + recent",
    "gov-policy": "Chinese government policy — search + recent",
    "grok": "Grok on X — ask blocked",
    "hackernews": "Hacker News — top, new, ask, show, jobs",
    "hf": "Hugging Face — top models",
    "hupu": "Hupu (虎扑) — Chinese sports community (like/reply blocked)",
    "imdb": "IMDb — movies, reviews, top, trending",
    "instagram": "Instagram — feeds (post/comment/follow/like/save/story/reel blocked)",
    "jd": "JD (京東) — products + reviews (add-cart blocked)",
    "jianyu": "Jianyu (剑鱼) — Chinese construction bid data",
    "jike": "即刻 Jike — feed + topics (comment/create/like/repost blocked)",
    "jimeng": "Jimeng (即梦) — AI image gen (generate/new blocked)",
    "ke": "Ke (贝壳) — Chinese real estate",
    "lesswrong": "LessWrong — rationality blog",
    "linkedin": "LinkedIn — profile + timeline search",
    "linux-do": "Linux.do — Chinese Linux community",
    "lobsters": "Lobsters — tech link aggregator",
    "maimai": "MaiMai — Chinese professional network",
    "medium": "Medium — articles and user feeds",
    "mubu": "Mubu (幕布) — Chinese outliner",
    "notebooklm": "Google NotebookLM — read notebooks and sources",
    "notion": "Notion — read docs (new/write blocked)",
    "nowcoder": "Nowcoder — Chinese dev jobs + interview prep",
    "ones": "ONES — project management (login/logout/worklog blocked)",
    "paperreview": "PaperReview — AI paper review (feedback/submit blocked)",
    "pixiv": "Pixiv — illustrations + rankings",
    "producthunt": "Product Hunt — today's products",
    "quark": "Quark cloud drive — ls/share-tree only (mkdir/mv/rm/save blocked)",
    "reddit": "Reddit — posts + comments (comment/save/subscribe/upvote blocked)",
    "reuters": "Reuters — news search",
    "sinablog": "Sina Blog — articles + hot",
    "sinafinance": "Sina Finance — Chinese stocks + news",
    "smzdm": "SMZDM (什么值得买) — Chinese deals",
    "spotify": "Spotify — search (playback controls + auth blocked)",
    "stackoverflow": "Stack Overflow — questions + search",
    "steam": "Steam — top sellers",
    "substack": "Substack — publications + feed",
    "taobao": "Taobao — Chinese e-commerce (add-cart blocked)",
    "tdx": "通达信 TDX — Chinese markets hot rank",
    "ths": "同花顺 THS — Chinese markets hot rank",
    "tieba": "Baidu Tieba — Chinese forum",
    "tiktok": "TikTok — feeds (post/follow/like/save/comment blocked)",
    "twitter": "Twitter/X — timeline, search, lists (all write actions blocked)",
    "uiverse": "Uiverse — UI component snippets",
    "v2ex": "V2EX — Chinese tech forum (daily sign-in blocked)",
    "wanfang": "Wanfang — Chinese academic papers",
    "web": "Web — generic URL fetch (read-only)",
    "weibo": "Weibo — microblogging",
    "weixin": "WeChat — download articles",
    "weread": "微信读书 — books + highlights + shelf",
    "wikipedia": "Wikipedia — articles, search, random",
    "xianyu": "閒魚 — second-hand marketplace (chat blocked)",
    "xiaoe": "小鹅通 — Chinese paid course platform",
    "xiaohongshu": "小红书 RedNote — lifestyle posts (publish blocked)",
    "xiaoyuzhou": "小宇宙 — Chinese podcast app",
    "xueqiu": "Xueqiu 雪球 — Chinese investor community",
    "yahoo-finance": "Yahoo Finance — global quotes",
    "yollomi": "Yollomi — AI image tools (all generate blocked)",
    "youtube": "YouTube — search, channel, transcript (like/subscribe blocked)",
    "yuanbao": "Tencent 元宝 — ask/new blocked (write-only site)",
    "zhihu": "Zhihu 知乎 — Q&A (answer/comment/favorite/follow/like blocked)",
    "zsxq": "ZSXQ 知识星球 — Chinese knowledge community",
}


# ---------------------------------------------------------------------------
# Global forbidden sub-commands (regardless of site).
# These are framework-level opencli verbs. Most aren't in SUPPORTED_SITES anyway,
# so the site allow-list already blocks them as top-level commands. Listed here
# as defence-in-depth in case a future site accidentally uses one of these
# names as a sub-command.
# ---------------------------------------------------------------------------

FORBIDDEN_GLOBAL: frozenset[str] = frozenset({
    "browser",    # any browser.<sub> — `eval`/`click`/`type` can inject/mutate
    "eval",       # explicit JS exec in page context
    "register",   # installs arbitrary external binaries
    "install",    # auto-runs brew / apt to install packages
    "plugin",     # installs GitHub packages as adapter plugins
    "daemon",     # installs the bridge daemon as a system service
    "adapter",    # adapter eject/reset/mutation
    "synthesize", # writes adapter code from capture data
    "record",     # cross-tab XHR/fetch injection recorder
    "exec",       # generic exec
    "shell",      # generic shell
})

# Backwards-compat alias — some code still imports FORBIDDEN_COMMANDS.
FORBIDDEN_COMMANDS: frozenset[str] = FORBIDDEN_GLOBAL


# ---------------------------------------------------------------------------
# Per-site write/mutation sub-commands — deny list.
# Only sites that have at least one write appear as keys. If a site is absent,
# it has no blocked sub-commands (every opencli sub-command for it is allowed).
#
# Methodology: ran `opencli <site> --help` for every site in SUPPORTED_SITES,
# classified each sub-command as READ (safe) or WRITE (mutates user account /
# remote state). All WRITE commands end up here. See `.claude/research/` for the
# raw categorization and `.claude/deployment-log.md` for the audit trail.
# ---------------------------------------------------------------------------

FORBIDDEN_PER_SITE: dict[str, frozenset[str]] = {
    "antigravity": frozenset({"model", "new", "send", "serve"}),
    "boss": frozenset({"batchgreet", "exchange", "greet", "invite", "mark", "send"}),
    "chatgpt": frozenset({"image"}),
    "chatgpt-app": frozenset({"ask", "model", "new", "send"}),
    "chatwise": frozenset({"ask", "model", "send"}),
    "codex": frozenset({"ask", "model", "send"}),
    "coupang": frozenset({"add-to-cart"}),
    "cursor": frozenset({"ask", "composer", "model", "send"}),
    "deepseek": frozenset({"ask", "new", "send"}),
    "discord-app": frozenset({"delete", "send"}),
    "doubao": frozenset({"ask", "new", "send"}),
    "doubao-app": frozenset({"ask", "new", "send"}),
    "douyin": frozenset({"delete", "draft", "publish", "update"}),
    "facebook": frozenset({"add-friend", "join-group"}),
    "gemini": frozenset({"ask", "deep-research", "image", "new"}),
    "grok": frozenset({"ask"}),
    "hupu": frozenset({"like", "reply", "unlike"}),
    "instagram": frozenset({
        "comment", "follow", "like", "note", "post", "reel",
        "save", "story", "unfollow", "unlike", "unsave",
    }),
    "jd": frozenset({"add-cart"}),
    "jike": frozenset({"comment", "create", "like", "repost"}),
    "jimeng": frozenset({"generate", "new"}),
    "notion": frozenset({"new", "write"}),
    "ones": frozenset({"login", "logout", "worklog"}),
    "paperreview": frozenset({"feedback", "submit"}),
    "quark": frozenset({"mkdir", "mv", "rename", "rm", "save"}),
    "reddit": frozenset({"comment", "save", "subscribe", "upvote"}),
    "spotify": frozenset({
        "auth", "next", "pause", "play", "prev", "queue",
        "repeat", "shuffle", "volume",
    }),
    "taobao": frozenset({"add-cart"}),
    "tiktok": frozenset({"comment", "follow", "like", "save", "unfollow", "unlike", "unsave"}),
    "twitter": frozenset({
        "accept", "block", "bookmark", "delete", "follow",
        "hide-reply", "like", "list-add", "list-remove",
        "post", "reply", "reply-dm", "unblock", "unbookmark", "unfollow",
    }),
    "v2ex": frozenset({"daily"}),
    "xianyu": frozenset({"chat"}),
    "xiaohongshu": frozenset({"publish"}),
    "youtube": frozenset({"like", "subscribe", "unlike", "unsubscribe"}),
    "yollomi": frozenset({
        "background", "edit", "face-swap", "generate",
        "object-remover", "remove-bg", "restore", "try-on",
        "upload", "upscale", "video",
    }),
    "yuanbao": frozenset({"ask", "new"}),
    "zhihu": frozenset({"answer", "comment", "favorite", "follow", "like"}),
}


# ---------------------------------------------------------------------------
# Full sub-command catalog per site — reads + writes combined.
#
# Source of truth for what `opencli <site> <cmd>` actually accepts. Used by
# `list_supported_sites` to tell the LLM which commands it can call, and by
# `check_whitelist` to reject guesses like `reddit fetch` or `web fetch`
# BEFORE they go over the wire (so the error message can hint the correct
# command name instead of a generic opencli failure).
#
# Derived from running `opencli <site> --help` for every site in
# SUPPORTED_SITES on opencli v1.7.7 (2026-04-24). Regenerate when bumping
# @jackwener/opencli — see the 2026-04-24 entry in `.claude/deployment-log.md`
# for the subagent-based methodology, raw output in `.claude/research/`.
# ---------------------------------------------------------------------------

SITE_COMMANDS: dict[str, frozenset[str]] = {
    "1688": frozenset({"assets", "download", "item", "search", "store"}),
    "36kr": frozenset({"article", "hot", "news", "search"}),
    "51job": frozenset({"company", "detail", "hot", "search"}),
    "amazon": frozenset({"bestsellers", "discussion", "movers-shakers", "new-releases", "offer", "product", "search"}),
    "antigravity": frozenset({"dump", "extract-code", "model", "new", "read", "send", "serve", "status", "watch"}),
    "apple-podcasts": frozenset({"episodes", "search", "top"}),
    "arxiv": frozenset({"paper", "search"}),
    "baidu-scholar": frozenset({"search"}),
    "band": frozenset({"bands", "mentions", "post", "posts"}),
    "barchart": frozenset({"flow", "greeks", "options", "quote"}),
    "bbc": frozenset({"news"}),
    "bilibili": frozenset({"comments", "download", "dynamic", "favorite", "feed", "feed-detail", "following", "history", "hot", "me", "ranking", "search", "subtitle", "user-videos", "video"}),
    "binance": frozenset({"asks", "depth", "gainers", "klines", "losers", "pairs", "price", "prices", "ticker", "top", "trades"}),
    "bloomberg": frozenset({"businessweek", "economics", "feeds", "industries", "main", "markets", "news", "opinions", "politics", "tech"}),
    "bluesky": frozenset({"feeds", "followers", "following", "profile", "search", "starter-packs", "thread", "trending", "user"}),
    "boss": frozenset({"batchgreet", "chatlist", "chatmsg", "detail", "exchange", "greet", "invite", "joblist", "mark", "recommend", "resume", "search", "send", "stats"}),
    "chaoxing": frozenset({"assignments", "exams"}),
    "chatgpt": frozenset({"image"}),
    "chatgpt-app": frozenset({"ask", "model", "new", "read", "send", "status"}),
    "chatwise": frozenset({"ask", "export", "history", "model", "read", "send"}),
    "cnki": frozenset({"search"}),
    "codex": frozenset({"ask", "export", "extract-diff", "history", "model", "read", "send"}),
    "coupang": frozenset({"add-to-cart", "search"}),
    "ctrip": frozenset({"search"}),
    "cursor": frozenset({"ask", "composer", "export", "extract-code", "history", "model", "read", "send"}),
    "deepseek": frozenset({"ask", "history", "new", "read", "send", "status"}),
    "devto": frozenset({"tag", "top", "user"}),
    "dictionary": frozenset({"examples", "search", "synonyms"}),
    "discord-app": frozenset({"channels", "delete", "members", "read", "search", "send", "servers", "status"}),
    "douban": frozenset({"book-hot", "download", "marks", "movie-hot", "photos", "reviews", "search", "subject", "top250"}),
    "doubao": frozenset({"ask", "detail", "history", "meeting-summary", "meeting-transcript", "new", "read", "send", "status"}),
    "doubao-app": frozenset({"ask", "dump", "new", "read", "screenshot", "send", "status"}),
    "douyin": frozenset({"activities", "collections", "delete", "draft", "drafts", "hashtag", "location", "profile", "publish", "stats", "update", "user-videos", "videos"}),
    "eastmoney": frozenset({"announcement", "convertible", "etf", "holders", "hot-rank", "index-board", "kline", "kuaixun", "longhu", "money-flow", "northbound", "quote", "rank", "sectors"}),
    "facebook": frozenset({"add-friend", "events", "feed", "friends", "groups", "join-group", "memories", "notifications", "profile", "search"}),
    "gemini": frozenset({"ask", "deep-research", "deep-research-result", "image", "new"}),
    "gitee": frozenset({"search", "trending", "user"}),
    "google": frozenset({"news", "search", "suggest", "trends"}),
    "google-scholar": frozenset({"search"}),
    "gov-law": frozenset({"recent", "search"}),
    "gov-policy": frozenset({"recent", "search"}),
    "grok": frozenset({"ask"}),
    "hackernews": frozenset({"ask", "best", "jobs", "new", "search", "show", "top", "user"}),
    "hf": frozenset({"top"}),
    "hupu": frozenset({"detail", "hot", "like", "mentions", "reply", "search", "unlike"}),
    "imdb": frozenset({"person", "reviews", "search", "title", "top", "trending"}),
    "instagram": frozenset({"comment", "download", "explore", "follow", "followers", "following", "like", "note", "post", "profile", "reel", "save", "saved", "search", "story", "unfollow", "unlike", "unsave", "user"}),
    "jd": frozenset({"add-cart", "cart", "detail", "item", "reviews", "search"}),
    "jianyu": frozenset({"detail", "search"}),
    "jike": frozenset({"comment", "create", "feed", "like", "notifications", "post", "repost", "search", "topic", "user"}),
    "jimeng": frozenset({"generate", "history", "new", "workspaces"}),
    "ke": frozenset({"chengjiao", "ershoufang", "xiaoqu", "zufang"}),
    "lesswrong": frozenset({"comments", "curated", "frontpage", "new", "read", "sequences", "shortform", "tag", "tags", "top", "top-month", "top-week", "top-year", "user", "user-posts"}),
    "linkedin": frozenset({"search", "timeline"}),
    "linux-do": frozenset({"categories", "category", "feed", "hot", "latest", "search", "tags", "topic", "topic-content", "user-posts", "user-topics"}),
    "lobsters": frozenset({"active", "hot", "newest", "tag"}),
    "maimai": frozenset({"search-talents"}),
    "medium": frozenset({"feed", "search", "user"}),
    "mubu": frozenset({"doc", "docs", "notes", "recent", "search"}),
    "notebooklm": frozenset({"current", "get", "history", "list", "note-list", "notes-get", "open", "source-fulltext", "source-get", "source-guide", "source-list", "status", "summary"}),
    "notion": frozenset({"export", "favorites", "new", "read", "search", "sidebar", "status", "write"}),
    "nowcoder": frozenset({"companies", "creators", "detail", "experience", "hot", "jobs", "notifications", "papers", "practice", "recommend", "referral", "salary", "search", "suggest", "topics", "trending"}),
    "ones": frozenset({"login", "logout", "me", "my-tasks", "task", "tasks", "token-info", "worklog"}),
    "paperreview": frozenset({"feedback", "review", "submit"}),
    "pixiv": frozenset({"detail", "download", "illusts", "ranking", "search", "user"}),
    "producthunt": frozenset({"browse", "hot", "posts", "today"}),
    "quark": frozenset({"ls", "mkdir", "mv", "rename", "rm", "save", "share-tree"}),
    "reddit": frozenset({"comment", "frontpage", "hot", "popular", "read", "save", "saved", "search", "subreddit", "subscribe", "upvote", "upvoted", "user", "user-comments", "user-posts"}),
    "reuters": frozenset({"search"}),
    "sinablog": frozenset({"article", "hot", "search", "user"}),
    "sinafinance": frozenset({"news", "rolling-news", "stock", "stock-rank"}),
    "smzdm": frozenset({"search"}),
    "spotify": frozenset({"auth", "next", "pause", "play", "prev", "queue", "repeat", "search", "shuffle", "status", "volume"}),
    "stackoverflow": frozenset({"bounties", "hot", "search", "unanswered"}),
    "steam": frozenset({"top-sellers"}),
    "substack": frozenset({"feed", "publication", "search"}),
    "taobao": frozenset({"add-cart", "cart", "detail", "reviews", "search"}),
    "tdx": frozenset({"hot-rank"}),
    "ths": frozenset({"hot-rank"}),
    "tieba": frozenset({"hot", "posts", "read", "search"}),
    "tiktok": frozenset({"comment", "explore", "follow", "following", "friends", "like", "live", "notifications", "profile", "save", "search", "unfollow", "unlike", "unsave", "user"}),
    "twitter": frozenset({"accept", "article", "block", "bookmark", "bookmarks", "delete", "download", "follow", "followers", "following", "hide-reply", "like", "likes", "list-add", "list-remove", "list-tweets", "lists", "notifications", "post", "profile", "reply", "reply-dm", "search", "thread", "timeline", "trending", "tweets", "unblock", "unbookmark", "unfollow"}),
    "uiverse": frozenset({"code", "preview"}),
    "v2ex": frozenset({"daily", "hot", "latest", "me", "member", "node", "nodes", "notifications", "replies", "topic", "user"}),
    "wanfang": frozenset({"search"}),
    "web": frozenset({"read"}),
    "weibo": frozenset({"comments", "feed", "hot", "me", "post", "search", "user"}),
    "weixin": frozenset({"download"}),
    "weread": frozenset({"ai-outline", "book", "highlights", "notebooks", "notes", "ranking", "search", "shelf"}),
    "wikipedia": frozenset({"random", "search", "summary", "trending"}),
    "xianyu": frozenset({"chat", "item", "search"}),
    "xiaoe": frozenset({"catalog", "content", "courses", "detail", "play-url"}),
    "xiaohongshu": frozenset({"comments", "creator-note-detail", "creator-notes", "creator-notes-summary", "creator-profile", "creator-stats", "download", "feed", "note", "notifications", "publish", "search", "user"}),
    "xiaoyuzhou": frozenset({"download", "episode", "podcast", "podcast-episodes", "transcript"}),
    "xueqiu": frozenset({"comments", "earnings-date", "feed", "fund-holdings", "fund-snapshot", "groups", "hot", "hot-stock", "kline", "search", "stock", "watchlist"}),
    "yahoo-finance": frozenset({"quote"}),
    "yollomi": frozenset({"background", "edit", "face-swap", "generate", "models", "object-remover", "remove-bg", "restore", "try-on", "upload", "upscale", "video"}),
    "youtube": frozenset({"channel", "comments", "feed", "history", "like", "playlist", "search", "subscribe", "subscriptions", "transcript", "unlike", "unsubscribe", "video", "watch-later"}),
    "yuanbao": frozenset({"ask", "new"}),
    "zhihu": frozenset({"answer", "comment", "download", "favorite", "follow", "hot", "like", "question", "search"}),
    "zsxq": frozenset({"dynamics", "groups", "search", "topic", "topics"}),
}


def blocked_commands_for(site: str) -> list[str]:
    """Sorted list of blocked sub-commands for a site (empty if none)."""
    return sorted(FORBIDDEN_PER_SITE.get(site, frozenset()))


def allowed_commands_for(site: str) -> list[str]:
    """Sorted list of sub-commands the LLM may dispatch for this site.

    = all sub-commands opencli exposes for that site, minus FORBIDDEN_GLOBAL,
    minus FORBIDDEN_PER_SITE[site]. Empty if `site` is unknown.
    """
    known = SITE_COMMANDS.get(site, frozenset())
    blocked = FORBIDDEN_PER_SITE.get(site, frozenset()) | FORBIDDEN_GLOBAL
    return sorted(known - blocked)


def check_whitelist(site: str, command: str) -> str | None:
    """Return an error message if (site, command) is not allowed, else None.

    Model (deny-list):
      1. Site must be in SUPPORTED_SITES.
      2. Command must not be in FORBIDDEN_GLOBAL (framework verbs).
      3. Command must not be in FORBIDDEN_PER_SITE[site] (write operations).
      4. Command must exist in SITE_COMMANDS[site] — rejecting unknown
         sub-commands with a hint saves a round-trip to opencli and gives
         the LLM a helpful error it can correct from.
    """
    if site not in SUPPORTED_SITES:
        return f"Site '{site}' is not supported."

    if command in FORBIDDEN_GLOBAL:
        return (
            f"Command '{command}' is globally forbidden "
            f"(framework-level verb; cannot run via fleet)."
        )

    blocked = FORBIDDEN_PER_SITE.get(site, frozenset())
    if command in blocked:
        return (
            f"Command '{site} {command}' is blocked — it is a write/mutation "
            f"action on your account, which fleet-mcp does not allow an LLM to perform. "
            f"Blocked for {site}: {', '.join(sorted(blocked))}."
        )

    known = SITE_COMMANDS.get(site)
    if known is not None and command not in known:
        allowed = allowed_commands_for(site)
        return (
            f"Command '{command}' is not a known opencli sub-command for site '{site}'. "
            f"Allowed for {site}: {', '.join(allowed)}."
        )

    return None


# ---------------------------------------------------------------------------
# Rate limiter — token bucket, in-memory
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple token-bucket rate limiter."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate  # tokens per second
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-node + global rate limiting."""

    def __init__(
        self,
        per_node_rpm: int = settings.rate_limit_per_node,
        global_rpm: int = settings.rate_limit_global,
    ) -> None:
        self._per_node_rpm = per_node_rpm
        self._global = _TokenBucket(rate=global_rpm / 60.0, burst=max(3, global_rpm // 10))
        self._nodes: dict[str, _TokenBucket] = {}

    def _get_node_bucket(self, node_id: str) -> _TokenBucket:
        if node_id not in self._nodes:
            self._nodes[node_id] = _TokenBucket(
                rate=self._per_node_rpm / 60.0,
                burst=3,
            )
        return self._nodes[node_id]

    def check(self, node_id: str) -> str | None:
        """Return error message if rate limit exceeded, else None."""
        if not self._global.allow():
            return "Global rate limit exceeded"
        if not self._get_node_bucket(node_id).allow():
            return f"Rate limit exceeded for node '{node_id}'"
        return None


rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Audit log — JSONL, args hashed, daily rotation
# ---------------------------------------------------------------------------

_AUDIT_PATH: Path = settings.audit_log_path


def _hash_args(args: dict[str, Any] | None) -> str:
    raw = json.dumps(args, sort_keys=True, default=str) if args else ""
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def audit_log(
    tool: str,
    *,
    node_id: str | None = None,
    site: str | None = None,
    command: str | None = None,
    args: dict[str, Any] | None = None,
    result: str = "ok",
    duration_ms: int | None = None,
    items_count: int | None = None,
) -> None:
    """Append one JSONL line to the audit log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
    }
    if node_id:
        entry["node_id"] = node_id
    if site:
        entry["site"] = site
    if command:
        entry["command"] = command
    if args is not None:
        entry["args_hash"] = _hash_args(args)
    entry["result"] = result
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if items_count is not None:
        entry["items_count"] = items_count

    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.warning("Failed to write audit log to %s", _AUDIT_PATH, exc_info=True)


# ---------------------------------------------------------------------------
# Output sanitization — strip sensitive fields recursively
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERN = re.compile(
    r"(cookie|session|token|x[-_]csrf[-_]token|authorization|"
    r"(api|access|secret)[-_]?key)",
    re.IGNORECASE,
)


def sanitize(obj: Any) -> Any:
    """Recursively strip fields whose names match sensitive patterns."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items() if not _SENSITIVE_PATTERN.search(k)}
    if isinstance(obj, list):
        return [sanitize(item) for item in obj]
    return obj
