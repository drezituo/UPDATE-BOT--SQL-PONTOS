import discord
from discord.ext import commands
import psycopg2
import os

# ---------------- INTENTS ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DATABASE ----------------
DATABASE_URL = os.getenv("DATABASE_URL")

conn = None
cursor = None


def get_db():
    global conn, cursor

    try:
        if conn is None:
            raise Exception("No connection")

        cursor.execute("SELECT 1")

    except:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()

    return cursor


# criar tabela se não existir
try:
    cursor = get_db()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER
        )
    """)
    conn.commit()
except:
    pass


# ---------------- BOT READY ----------------
@bot.event
async def on_ready():
    print(f"✅ Bot ligado como {bot.user}")


# ---------------- ADD PONTOS ----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):

    cursor = get_db()

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )

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

    await ctx.send(f"✅ {membro.display_name} agora tem **{novo_total} pontos**")


# ---------------- REMOVE PONTOS ----------------
@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):

    cursor = get_db()

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
        "UPDATE pontos SET pontos = %s WHERE user_id = %s",
        (novo_total, membro.id)
    )

    conn.commit()

    await ctx.send(f"❌ {membro.display_name} agora tem **{novo_total} pontos**")


# ---------------- VER PONTOS ----------------
@bot.command()
async def pontos(ctx, membro: discord.Member = None):

    cursor = get_db()

    membro = membro or ctx.author

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )

    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0

    await ctx.send(f"⭐ {membro.display_name} tem **{total} pontos**")


# ---------------- RANKING ----------------
@bot.command()
async def ranking(ctx):

    cursor = get_db()

    cursor.execute(
        "SELECT user_id, pontos FROM pontos ORDER BY pontos DESC"
    )

    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos registrados.")
        return

    mensagem = "**🏆 Ranking de Pontos:**\n"

    for i, (user_id, pontos) in enumerate(resultados, start=1):

        membro = ctx.guild.get_member(user_id)

        nome = membro.display_name if membro else "Usuário desconhecido"

        linha = f"{i}. {nome} — {pontos} pontos\n"

        if len(mensagem) + len(linha) > 2000:
            await ctx.send(mensagem)
            mensagem = ""

        mensagem += linha

    if mensagem:
        await ctx.send(mensagem)


# ---------------- RUN BOT ----------------
bot.run(os.getenv("DISCORD_TOKEN"))
