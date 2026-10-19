import asyncio
import logging
import os
from collections import defaultdict

import discord
from discord import app_commands
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("groq-discord-ai")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly AI assistant chatting in Discord. "
    "Be clear and concise, and use Markdown when it helps.",
)
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "8"))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing. Add it to your .env file.")

# Groq's Python client is synchronous, so requests are run in a worker thread.
groq = Groq(api_key=GROQ_API_KEY)

# Keep separate short-term conversations per Discord user and server.
# This starter stores history only in memory; it resets when the bot restarts.
histories: dict[tuple[int | None, int], list[dict[str, str]]] = defaultdict(list)
locks: dict[tuple[int | None, int], asyncio.Lock] = defaultdict(asyncio.Lock)


class AIDiscordBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # Global slash commands can take a little while to appear in Discord.
        synced = await self.tree.sync()
        log.info("Synced %d global slash command(s)", len(synced))


bot = AIDiscordBot()


@bot.event
async def on_ready() -> None:
    log.info("Connected as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")


def split_for_discord(text: str, limit: int = 1900) -> list[str]:
    """Split a response into safely sized Discord messages."""
    chunks: list[str] = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = text.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        chunks.append(text)
    return chunks or ["I couldn't produce a text response. Please try again."]


@bot.tree.command(name="ask", description="Ask the AI assistant a question")
@app_commands.describe(prompt="What would you like to ask?")
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
async def ask(interaction: discord.Interaction, prompt: str) -> None:
    if len(prompt) > 4000:
        await interaction.response.send_message(
            "Please keep your question under 4,000 characters.", ephemeral=True
        )
        return

    await interaction.response.defer(thinking=True)
    key = (interaction.guild_id, interaction.user.id)

    async with locks[key]:
        previous = histories[key]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(previous)
        messages.append({"role": "user", "content": prompt})

        try:
            completion = await asyncio.to_thread(
                groq.chat.completions.create,
                model=GROQ_MODEL,
                messages=messages,
                max_tokens=1024,
                temperature=0.7,
            )
            answer = completion.choices[0].message.content or "I couldn't produce a response."
        except Exception:
            log.exception("Groq request failed")
            await interaction.followup.send(
                "Sorry, I couldn't reach the AI service just now. Please try again in a moment."
            )
            return

        # Save only successful exchanges and retain a bounded number of turns.
        previous.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )
        histories[key] = previous[-(MAX_HISTORY_TURNS * 2) :]

    for index, chunk in enumerate(split_for_discord(answer)):
        if index == 0:
            await interaction.followup.send(chunk)
        else:
            await interaction.followup.send(chunk)


@bot.tree.command(name="clear", description="Clear your AI conversation history in this server")
async def clear(interaction: discord.Interaction) -> None:
    key = (interaction.guild_id, interaction.user.id)
    async with locks[key]:
        histories.pop(key, None)
    await interaction.response.send_message(
        "Your conversation history has been cleared.", ephemeral=True
    )


@bot.tree.command(name="ping", description="Check whether the bot is online")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("Pong! I'm online.", ephemeral=True)


@ask.error
async def ask_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"Please wait {error.retry_after:.1f} seconds before asking again."
    else:
        log.exception("Slash command error", exc_info=error)
        message = "Something went wrong while running that command."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


bot.run(DISCORD_TOKEN)
