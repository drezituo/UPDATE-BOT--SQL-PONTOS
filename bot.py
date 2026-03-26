# bot.py
import discord
from discord.ext import commands
import psycopg2
from psycopg2.extras import RealDictCursor
import os

# ---------- CONFIGURAÇÃO ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # ex: postgres://user:pass@host:port/dbname

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- CONEXÃO COM O BANCO ----------
def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ---------- COMANDOS ----------
@bot.command(name="pontos")
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
        resultado = cursor.fetchone()
        pontos = resultado['pontos'] if resultado else 0
        await ctx.send(f"{membro.display_name} tem {pontos} ponto(s).")
    except Exception as e:
        await ctx.send(f"Ocorreu um erro: {e}")
    finally:
        cursor.close()
        conn.close()

@bot.command(name="ranking")
async def ranking(ctx):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
        resultados = cursor.fetchall()
        
        mensagem = "**🏆 Ranking de Pontos:**\n"
        contador = 1

        for r in resultados:
            user_id = r['user_id']
            pontos = r['pontos']
            user = ctx.guild.get_member(user_id)
            nome = user.display_name if user else f"ID:{user_id}"
            linha = f"{contador}. {nome} — {pontos} ponto(s)\n"
            
            if len(mensagem) + len(linha) > 2000:
                await ctx.send(mensagem)
                mensagem = ""
            mensagem += linha
            contador += 1

        if mensagem:
            await ctx.send(mensagem)
    except Exception as e:
        await ctx.send(f"Ocorreu um erro: {e}")
    finally:
        cursor.close()
        conn.close()

# ---------- INICIAR BOT ----------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")

bot.run(TOKEN)
