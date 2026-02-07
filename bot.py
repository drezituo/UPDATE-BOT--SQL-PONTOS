import discord
from discord.ext import commands
import sqlite3
import os

# ---------- INTENTS ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
conn = sqlite3.connect("pontos.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos (
    user_id INTEGER PRIMARY KEY,
    nome TEXT,
    pontos INTEGER
)
""")
conn.commit()

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    print(f"✅ Bot ligado como {bot.user}")

# ---------- COMMANDS ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = ?",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute(
            "UPDATE pontos SET pontos = ?, nome = ? WHERE user_id = ?",
            (novo_total, membro.display_name, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, nome, pontos) VALUES (?, ?, ?)",
            (membro.id, membro.display_name, quantidade)
        )

    conn.commit()
    await ctx.send(f"✅ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = ?",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    if not resultado:
        await ctx.send("⚠️ Esse usuário não tem pontos.")
        return

    novo_total = max(resultado[0] - quantidade, 0)
    cursor.execute(
        "UPDATE pontos SET pontos = ?, nome = ? WHERE user_id = ?",
        (novo_total, membro.display_name, membro.id)
    )
    conn.commit()

    await ctx.send(f"❌ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = ?",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0
    await ctx.send(f"⭐ {membro.mention} tem **{total} pontos**")

@bot.command()
async def ranking(ctx):
    cursor.execute(
        "SELECT nome, pontos FROM pontos ORDER BY pontos DESC LIMIT 10"
    )
    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos registrados.")
        return

    mensagem = "**🏆 Ranking de Pontos:**\n"
    for i, (nome, pontos) in enumerate(resultados, start=1):
        mensagem += f"{i}. {nome} — {pontos} pontos\n"

    await ctx.send(mensagem)

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
