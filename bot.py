import discord
from discord.ext import commands
import os
import psycopg2

# ---------- INTENTS ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE (POSTGRESQL - RAILWAY) ----------
DATABASE_URL = os.getenv("DATABASE_URL")

# Cria conexão e cursor
conn = psycopg2.connect(DATABASE_URL, sslmode='require')
cursor = conn.cursor()

# Cria tabela se não existir
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos (
    user_id BIGINT PRIMARY KEY,
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
    # Define nome completo evitando None
    nome_usuario = membro.nick if membro.nick else membro.name

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute(
            "UPDATE pontos SET pontos = %s, nome = %s WHERE user_id = %s",
            (novo_total, nome_usuario, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, nome, pontos) VALUES (%s, %s, %s)",
            (membro.id, nome_usuario, quantidade)
        )

    conn.commit()
    await ctx.send(f"✅ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    nome_usuario = membro.nick if membro.nick else membro.name

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    if not resultado:
        await ctx.send("⚠️ Esse usuário não tem pontos.")
        return

    novo_total = max(resultado[0] - quantidade, 0)
    cursor.execute(
        "UPDATE pontos SET pontos = %s, nome = %s WHERE user_id = %s",
        (novo_total, nome_usuario, membro.id)
    )
    conn.commit()

    await ctx.send(f"❌ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    nome_usuario = membro.nick if membro.nick else membro.name

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0
    await ctx.send(f"⭐ {membro.mention} tem **{total} pontos**")

@bot.command()
async def ranking(ctx):
    cursor.execute(
        "SELECT user_id, pontos, nome FROM pontos ORDER BY pontos DESC"
    )
    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos registrados.")
        return

    mensagem = "**🏆 Ranking de Pontos:**\n"
    for i, (user_id, pontos, nome) in enumerate(resultados, start=1):
        # Garante que nome não seja None
        nome_final = nome if nome else f"<@{user_id}>"
        linha = f"{i}. {nome_final} — {pontos} pontos\n"

        # Evita ultrapassar limite de 2000 caracteres
        if len(mensagem) + len(linha) > 2000:
            await ctx.send(mensagem)
            mensagem = ""
        mensagem += linha

    if mensagem:
        await ctx.send(mensagem)

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
