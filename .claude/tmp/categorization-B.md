# Categorization batch B

Sites in this batch: 34. Research date: 2026-04-24. opencli: 1.7.7.

## facebook

**Reads:** `events`, `feed`, `friends`, `groups`, `memories`, `notifications`, `profile`, `search`
**Writes:** `add-friend`, `join-group`

## gemini

**Reads:** `ask`, `deep-research-result`, `image`
**Writes:** `deep-research`, `new`
**Unsure:** `ask` — sends a prompt to an LLM (creates a conversation turn server-side), but only returns the response; treating as READ since no user-state mutation persists beyond ephemeral chat history.

## gitee

**Reads:** `search`, `trending`, `user`
**Writes:**

## google

**Reads:** `news`, `search`, `suggest`, `trends`
**Writes:**

## google-scholar

**Reads:** `search`
**Writes:**

## gov-law

**Reads:** `recent`, `search`
**Writes:**

## gov-policy

**Reads:** `recent`, `search`
**Writes:**

## grok

**Reads:** `ask`
**Writes:**
**Unsure:** `ask` — sends message to Grok creating a conversation turn; treated as READ since no persistent user-state mutation beyond ephemeral chat.

## hackernews

**Reads:** `ask`, `best`, `jobs`, `new`, `search`, `show`, `top`, `user`
**Writes:**

## hf

**Reads:** `top`
**Writes:**

## hupu

**Reads:** `detail`, `hot`, `mentions`, `search`
**Writes:** `like`, `reply`, `unlike`

## imdb

**Reads:** `person`, `reviews`, `search`, `title`, `top`, `trending`
**Writes:**

## instagram

**Reads:** `download`, `explore`, `followers`, `following`, `profile`, `saved`, `search`, `user`
**Writes:** `comment`, `follow`, `like`, `note`, `post`, `reel`, `save`, `story`, `unfollow`, `unlike`, `unsave`

## jd

**Reads:** `cart`, `detail`, `item`, `reviews`, `search`
**Writes:** `add-cart`

## jianyu

**Reads:** `detail`, `search`
**Writes:**

## jike

**Reads:** `feed`, `notifications`, `post`, `search`, `topic`, `user`
**Writes:** `comment`, `create`, `like`, `repost`

## jimeng

**Reads:** `history`, `workspaces`
**Writes:** `generate`, `new`

## ke

**Reads:** `chengjiao`, `ershoufang`, `xiaoqu`, `zufang`
**Writes:**

## lesswrong

**Reads:** `comments`, `curated`, `frontpage`, `new`, `read`, `sequences`, `shortform`, `tag`, `tags`, `top`, `top-month`, `top-week`, `top-year`, `user`, `user-posts`
**Writes:**

## linkedin

**Reads:** `search`, `timeline`
**Writes:**

## linux-do

**Reads:** `categories`, `category`, `feed`, `hot`, `latest`, `search`, `tags`, `topic`, `topic-content`, `user-posts`, `user-topics`
**Writes:**

## lobsters

**Reads:** `active`, `hot`, `newest`, `tag`
**Writes:**

## maimai

**Reads:** `search-talents`
**Writes:**

## medium

**Reads:** `feed`, `search`, `user`
**Writes:**

## mubu

**Reads:** `doc`, `docs`, `notes`, `recent`, `search`
**Writes:**

## notebooklm

**Reads:** `current`, `get`, `history`, `list`, `note-list`, `notes-get`, `source-fulltext`, `source-get`, `source-guide`, `source-list`, `status`, `summary`
**Writes:** `open`
**Unsure:** `open` — "Open one NotebookLM notebook in the automation workspace by id or URL"; selects/changes active notebook context in workspace (mutates UI state, not data). Classified as WRITE since it changes workspace state; reasonable to treat as READ-ish navigation, flagging for review.

## notion

**Reads:** `export`, `favorites`, `read`, `search`, `sidebar`, `status`
**Writes:** `new`, `write`

## nowcoder

**Reads:** `companies`, `creators`, `detail`, `experience`, `hot`, `jobs`, `notifications`, `papers`, `practice`, `recommend`, `referral`, `salary`, `search`, `suggest`, `topics`, `trending`
**Writes:**

## ones

**Reads:** `me`, `my-tasks`, `task`, `tasks`, `token-info`
**Writes:** `login`, `logout`, `worklog`

## paperreview

**Reads:** `review`
**Writes:** `feedback`, `submit`

## pixiv

**Reads:** `detail`, `download`, `illusts`, `ranking`, `search`, `user`
**Writes:**

## producthunt

**Reads:** `browse`, `hot`, `posts`, `today`
**Writes:**

## quark

**Reads:** `ls`, `share-tree`
**Writes:** `mkdir`, `mv`, `rename`, `rm`, `save`

## reddit

**Reads:** `frontpage`, `hot`, `popular`, `read`, `saved`, `search`, `subreddit`, `upvoted`, `user`, `user-comments`, `user-posts`
**Writes:** `comment`, `save`, `subscribe`, `upvote`
