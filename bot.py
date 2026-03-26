import discord
from discord.ext import commands
import psycopg2
import os

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
def get_connection():
    return psycopg2.connect(DATABASE_URL)

# Criar tabela (não apaga dados existentes)
conn = get_connection()
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos (
    user_id BIGINT PRIMARY KEY,
    pontos INTEGER DEFAULT 0
)
""")
conn.commit()
cursor.close()
conn.close()

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    print(f"✅ Bot ligado como {bot.user}")

# ---------- ADD PONTOS ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute(
            "UPDATE pontos SET pontos = %s WHERE user_id = %s",
            (novo_total, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, pontos) VALUES (%s, %s)",
            (membro.id, quantidade)
        )

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"✅ {membro.display_name} agora tem **{novo_total} pontos**")

# ---------- REMOVE PONTOS ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    if not resultado:
        await ctx.send("⚠️ Esse usuário não tem pontos.")
        cursor.close()
        conn.close()
        return

    novo_total = max(resultado[0] - quantidade, 0)

    cursor.execute(
        "UPDATE pontos SET pontos = %s WHERE user_id = %s",
        (novo_total, membro.id)
    )

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{novo_total} pontos**")

# ---------- VER PONTOS ----------
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

# ---------- RANKING ----------
@bot.command()
async def ranking(ctx):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos registrados.")
        cursor.close()
        conn.close()
        return

    mensagem = "**🏆 Ranking de Pontos:**\n"

    for i, (user_id, pontos) in enumerate(resultados, start=1):
        membro = ctx.guild.get_member(user_id)
        nome = membro.display_name if membro else f"ID:{user_id}"

        linha = f"{i}. {nome} — {pontos} pontos\n"

        # Dividir mensagens se passar limite do Discord
        if len(mensagem) + len(linha) > 2000:
            await ctx.send(mensagem)
            mensagem = ""

        mensagem += linha

    if mensagem:
        await ctx.send(mensagem)

    cursor.close()
    conn.close()

# ---------- RUN ----------
bot.run(TOKEN)
