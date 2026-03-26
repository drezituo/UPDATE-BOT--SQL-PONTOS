import discord
from discord.ext import commands
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Conexão com PostgreSQL
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Criação da tabela caso não exista
cursor.execute("""
CREATE TABLE IF NOT EXISTS pontos (
    user_id BIGINT PRIMARY KEY,
    pontos INTEGER NOT NULL DEFAULT 0
)
""")
conn.commit()

# Comando para adicionar pontos
@bot.command()
async def addpontos(ctx, membro: discord.Member, pontos: int):
    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    result = cursor.fetchone()
    if result:
        cursor.execute("UPDATE pontos SET pontos = pontos + %s WHERE user_id = %s", (pontos, membro.id))
    else:
        cursor.execute("INSERT INTO pontos (user_id, pontos) VALUES (%s, %s)", (membro.id, pontos))
    conn.commit()
    await ctx.send(f"{membro.display_name} recebeu {pontos} pontos!")

# Comando para ver pontos
@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    if not membro:
        membro = ctx.author
    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    result = cursor.fetchone()
    pts = result[0] if result else 0
    await ctx.send(f"{membro.display_name} tem {pts} pontos.")

# Comando para ranking
@bot.command()
async def ranking(ctx):
    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    result = cursor.fetchall()

    mensagem = "**🏆 Ranking de Pontos:**\n"
    for i, (user_id, pts) in enumerate(result, 1):
        member = ctx.guild.get_member(user_id)
        nome = member.display_name if member else "Desconhecido"
        linha = f"{i}. {nome} — {pts} pontos\n"
        # Se ultrapassar 2000 caracteres, envia e reinicia mensagem
        if len(mensagem) + len(linha) > 2000:
            await ctx.send(mensagem)
            mensagem = ""
        mensagem += linha
    if mensagem:
        await ctx.send(mensagem)

@bot.event
async def on_ready():
    print(f"Bot {bot.user} conectado e pronto!")

bot.run(TOKEN)
