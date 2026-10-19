import asyncio
import logging
import os
import re
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
MESSAGE_COOLDOWN_SECONDS = 5

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing. Add it to your .env file.")

# Groq's Python client is synchronous, so requests run in a worker thread.
groq = Groq(api_key=GROQ_API_KEY)

# Separate short-term conversations by server and user. This history is in-memory
# only and is erased when the bot restarts.
ConversationKey = tuple[int | None, int]
histories: dict[ConversationKey, list[dict[str, str]]] = defaultdict(list)
locks: dict[ConversationKey, asyncio.Lock] = defaultdict(asyncio.Lock)
last_message_request: dict[ConversationKey, float] = {}


class AIDiscordBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Required to read messages so the bot can detect mentions and replies.
        intents.message_content = True
        super().__init__(intents=intents)
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
    """Split an AI response into safely sized Discord messages."""
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


async def generate_reply(key: ConversationKey, prompt: str) -> str:
    """Ask Groq using this user's recent conversation and save the exchange."""
    async with locks[key]:
        previous = histories[key]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(previous)
        messages.append({"role": "user", "content": prompt})

        completion = await asyncio.to_thread(
            groq.chat.completions.create,
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.7,
        )
        answer = completion.choices[0].message.content or "I couldn't produce a response."

        previous.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )
        histories[key] = previous[-(MAX_HISTORY_TURNS * 2) :]
        return answer


async def is_reply_to_bot(message: discord.Message) -> bool:
    """Check whether a Discord reply references a message written by this bot."""
    if not message.reference or not bot.user:
        return False

    referenced = message.reference.resolved
    if isinstance(referenced, discord.Message):
        return referenced.author.id == bot.user.id

    if message.reference.message_id:
        try:
            referenced = await message.channel.fetch_message(message.reference.message_id)
            return referenced.author.id == bot.user.id
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
    return False


@bot.event
async def on_message(message: discord.Message) -> None:
    # Only respond in servers, and never respond to bots (including ourselves).
    if message.guild is None or message.author.bot or bot.user is None:
        return

    was_mentioned = bot.user in message.mentions
    was_reply = await is_reply_to_bot(message)
    if not (was_mentioned or was_reply):
        return

    prompt = re.sub(rf"<@!?{bot.user.id}>", "", message.content).strip()
    if not prompt:
        prompt = "The user mentioned or replied to you without adding a question. Ask what they need help with."

    key: ConversationKey = (message.guild.id, message.author.id)
    now = asyncio.get_running_loop().time()
    last_request = last_message_request.get(key, 0)
    if now - last_request < MESSAGE_COOLDOWN_SECONDS:
        await message.reply(
            "Give me a few seconds before asking again.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    last_message_request[key] = now

    try:
        async with message.channel.typing():
            answer = await generate_reply(key, prompt)
    except Exception:
        log.exception("Groq request failed for a Discord message")
        await message.reply(
            "Sorry, I couldn't reach the AI service just now. Please try again in a moment.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    chunks = split_for_discord(answer)
    await message.reply(
        chunks[0],
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    for chunk in chunks[1:]:
        await message.channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


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
    key: ConversationKey = (interaction.guild_id, interaction.user.id)
    try:
        answer = await generate_reply(key, prompt)
    except Exception:
        log.exception("Groq request failed for /ask")
        await interaction.followup.send(
            "Sorry, I couldn't reach the AI service just now. Please try again in a moment."
        )
        return

    for chunk in split_for_discord(answer):
        await interaction.followup.send(chunk)


@bot.tree.command(name="clear", description="Clear your AI conversation history in this server")
async def clear(interaction: discord.Interaction) -> None:
    key: ConversationKey = (interaction.guild_id, interaction.user.id)
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
