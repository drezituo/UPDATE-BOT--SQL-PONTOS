import discord
from discord.ext import commands
import psycopg2
import os

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
def get_connection():
    return psycopg2.connect(DATABASE_URL)

# ---------- CRIAR TABELAS ----------
conn = get_connection()
cursor = conn.cursor()

# Pontos normais (já tens)
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos (
    user_id BIGINT PRIMARY KEY,
    pontos INTEGER DEFAULT 0
)
""")

# SOLO
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos_solo (
    user_id BIGINT PRIMARY KEY,
    pontos INTEGER DEFAULT 0
)
""")

# TEAM
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos_team (
    user_id BIGINT PRIMARY KEY,
    pontos INTEGER DEFAULT 0
)
""")

conn.commit()
cursor.close()
conn.close()

# ---------- EVENT ----------
@bot.event
async def on_ready():
    print(f"✅ Bot ligado como {bot.user}")

# =========================
# 🔥 SISTEMA ANTIGO (INALTERADO)
# =========================

@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute("UPDATE pontos SET pontos = %s WHERE user_id = %s", (novo_total, membro.id))
    else:
        novo_total = quantidade
        cursor.execute("INSERT INTO pontos (user_id, pontos) VALUES (%s, %s)", (membro.id, quantidade))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"✅ {membro.display_name} agora tem **{novo_total} pontos**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if not resultado:
        await ctx.send("⚠️ Esse usuário não tem pontos.")
        return

    novo_total = max(resultado[0] - quantidade, 0)
    cursor.execute("UPDATE pontos SET pontos = %s WHERE user_id = %s", (novo_total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{novo_total} pontos**")

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0

    cursor.close()
    conn.close()

    await ctx.send(f"⭐ {membro.display_name} tem **{total} pontos**")

# =========================
# 🆕 SOLO REBIRTH
# =========================

@bot.command()
@commands.has_permissions(administrator=True)
async def addsolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_solo SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_solo VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"🔥 {membro.display_name} agora tem **{total} vitórias no Solo Rebirth**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removesolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        await ctx.send("⚠️ Sem dados.")
        return

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_solo SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no Solo Rebirth**")

@bot.command()
async def pontossolo(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"🎯 {membro.display_name} tem **{total} vitórias no Solo Rebirth!**")

@bot.command()
async def rankingsolo(ctx):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_solo ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking Solo Rebirth:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} vitórias\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 TEAM REBIRTH
# =========================

@bot.command()
@commands.has_permissions(administrator=True)
async def addteam(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_team SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_team VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"👥 {membro.display_name} agora tem **{total} vitórias no Team Rebirth**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removeteam(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        await ctx.send("⚠️ Sem dados.")
        return

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_team SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no Team Rebirth**")

@bot.command()
async def pontosteam(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"👥 {membro.display_name} tem **{total} vitórias no Team Rebirth!**")

@bot.command()
async def rankingteam(ctx):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_team ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking Team Rebirth:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} vitórias\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# ---------- RUN ----------
bot.run(TOKEN)
