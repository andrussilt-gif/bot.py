import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import aiohttp
import feedparser
import json
import os
from datetime import datetime

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

PREFIX = "!"
intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Simple storage for server settings
SETTINGS_FILE = "settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_settings(data):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=4)

settings = load_settings()

# ----------------- EVENTS -----------------

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    twitch_check.start()
    youtube_check.start()

# ----------------- MODERATION -----------------

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"🔨 Kicked {member.mention} | Reason: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    await member.ban(reason=reason)
    await ctx.send(f"⛔ Banned {member.mention} | Reason: {reason}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Cleared {amount} messages.", delete_after=5)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def roleadd(ctx, member: discord.Member, *, role_name):
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.add_roles(role)
        await ctx.send(f"✅ Added role `{role_name}` to {member.mention}")
    else:
        await ctx.send("❌ Role not found.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def roleremove(ctx, member: discord.Member, *, role_name):
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.remove_roles(role)
        await ctx.send(f"✅ Removed role `{role_name}` from {member.mention}")
    else:
        await ctx.send("❌ Role not found.")

# ----------------- ANNOUNCEMENT CHANNEL CONFIG -----------------

@bot.command()
@commands.has_permissions(administrator=True)
async def setannounce(ctx, channel: discord.TextChannel):
    settings[str(ctx.guild.id)] = {"announce_channel": channel.id}
    save_settings(settings)
    await ctx.send(f"📢 Announcement channel set to {channel.mention}")

def get_announce_channel(guild):
    data = settings.get(str(guild.id))
    if data and "announce_channel" in data:
        return guild.get_channel(data["announce_channel"])
    return None

# ----------------- TWITCH CHECK -----------------

TWITCH_STREAMER = "Ch0ppyyy"
twitch_token = None
last_live_status = False

async def get_twitch_token():
    global twitch_token
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as resp:
            data = await resp.json()
            twitch_token = data["access_token"]

async def check_twitch_live():
    global last_live_status
    if not twitch_token:
        await get_twitch_token()

    url = f"https://api.twitch.tv/helix/streams?user_login={TWITCH_STREAMER}"
    headers = {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {twitch_token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if data["data"]:
                if not last_live_status:
                    last_live_status = True
                    return data["data"][0]
            else:
                last_live_status = False
    return None

@tasks.loop(minutes=5)
async def twitch_check():
    stream_data = await check_twitch_live()
    if stream_data:
        for guild in bot.guilds:
            channel = get_announce_channel(guild)
            if channel:
                embed = discord.Embed(
                    title=f"{TWITCH_STREAMER} is LIVE!",
                    description=stream_data["title"],
                    color=discord.Color.purple(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Game", value=stream_data["game_name"])
                embed.set_image(url=stream_data["thumbnail_url"].replace("{width}", "1920").replace("{height}", "1080"))
                await channel.send(embed=embed)

# ----------------- YOUTUBE CHECK -----------------

YOUTUBE_CHANNEL_ID = "UC9sdzpIS6s3fPHIr2aL9jCA"
last_video_id = None

async def fetch_latest_youtube():
    global last_video_id
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    feed = feedparser.parse(feed_url)
    if feed.entries:
        latest = feed.entries[0]
        if latest.yt_videoid != last_video_id:
            last_video_id = latest.yt_videoid
            return latest
    return None

@tasks.loop(minutes=10)
async def youtube_check():
    video = await fetch_latest_youtube()
    if video:
        for guild in bot.guilds:
            channel = get_announce_channel(guild)
            if channel:
                embed = discord.Embed(
                    title=f"New YouTube Video: {video.title}",
                    url=video.link,
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                embed.set_image(url=f"https://img.youtube.com/vi/{video.yt_videoid}/maxresdefault.jpg")
                await channel.send(embed=embed)

# ----------------- MUSIC COMMANDS -----------------

ytdl_opts = {
    'format': 'bestaudio',
    'noplaylist': True,
    'quiet': True
}
ytdl = yt_dlp.YoutubeDL(ytdl_opts)

queues = {}

def play_next(ctx):
    if queues[ctx.guild.id]:
        url = queues[ctx.guild.id].pop(0)
        ctx.voice_client.play(discord.FFmpegPCMAudio(url, executable="ffmpeg"), after=lambda e: play_next(ctx))

@bot.command()
async def play(ctx, *, search):
    if not ctx.author.voice:
        return await ctx.send("❌ You must be in a voice channel.")
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()

    info = ytdl.extract_info(f"ytsearch:{search}", download=False)['entries'][0]
    url = info['url']
    title = info['title']

    if ctx.guild.id in queues and ctx.voice_client.is_playing():
        queues[ctx.guild.id].append(url)
        await ctx.send(f"➕ Added to queue: **{title}**")
    else:
        queues[ctx.guild.id] = []
        ctx.voice_client.play(discord.FFmpegPCMAudio(url, executable="ffmpeg"), after=lambda e: play_next(ctx))
        await ctx.send(f"🎶 Now playing: **{title}**")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭ Skipped current track.")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()

# ----------------- RUN BOT -----------------
bot.run(TOKEN)
