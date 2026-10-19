# Groq-powered Discord AI bot

A beginner-friendly Discord bot with `/ask`, `/clear`, and `/ping` slash commands. `/ask` uses Groq's API and keeps a short, temporary conversation history for each user in each server. History is held in memory and is erased when the bot restarts.

## 1. Create the API keys

- Create a Discord application and bot at the [Discord Developer Portal](https://discord.com/developers/applications). Copy the bot token from the **Bot** section.
- Create a Groq API key from the [Groq Console](https://console.groq.com/keys). Check Groq's current model list and choose a model available to your account.
- Never post either key in a chat, screenshot, public repository, or support ticket.

## 2. Invite the bot to your server

In the Developer Portal, open **OAuth2 → URL Generator** and select these scopes:

- `bot`
- `applications.commands`

Select only the bot permissions you need. For this starter, **Send Messages** is enough. Open the generated URL and authorize the bot for a server you manage.

## 3. Install and configure

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Then install dependencies and make your local settings file:

```bash
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys. For example:

```env
DISCORD_TOKEN=your_discord_bot_token
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile
```

The configured model is an example; model availability can change. Set `GROQ_MODEL` to a model currently available to your Groq account.

## 4. Start the bot

```bash
python bot.py
```

When it logs in successfully, try `/ask`, `/clear`, or `/ping` in Discord. The first global slash-command registration may take a little while to show up.

## Configuration options

- `SYSTEM_PROMPT`: changes the assistant's behavior.
- `MAX_HISTORY_TURNS`: how many previous user/assistant exchanges to keep (default `8`).
- `GROQ_MODEL`: the Groq model name.

## Safety and privacy notes

- `.env` is ignored by Git; do not remove that protection or commit secrets.
- User prompts and retained conversation context are sent to Groq to generate replies. Review the provider's current privacy and retention terms before deploying this bot for others.
- The included conversation history is in-memory only, not a permanent database.
- The `/ask` command is rate-limited to one request per user every five seconds. API usage can still incur limits or costs depending on your Groq account and current plan.
