import discord
from discord.ext import commands
from discord.ui import View, Button
import os
import psycopg2

# ---------- INTENTS ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE (POSTGRESQL - RAILWAY) ----------
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

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
    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    if resultado:
        novo_total = resultado[0] + quantidade
        cursor.execute(
            "UPDATE pontos SET pontos = %s, nome = %s WHERE user_id = %s",
            (novo_total, membro.display_name, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, nome, pontos) VALUES (%s, %s, %s)",
            (membro.id, membro.display_name, quantidade)
        )

    conn.commit()
    await ctx.send(f"✅ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
@commands.has_permissions(administrator=True)
async def removepontos(ctx, membro: discord.Member, quantidade: int):
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
        (novo_total, membro.display_name, membro.id)
    )
    conn.commit()

    await ctx.send(f"❌ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    cursor.execute(
        "SELECT pontos FROM pontos WHERE user_id = %s",
        (membro.id,)
    )
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0
    await ctx.send(f"⭐ {membro.mention} tem **{total} pontos**")

# ---------- COMMAND: PAGINATED RANKING COM TOP 10 INICIAL ----------
@bot.command()
async def ranking(ctx):
    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos registrados.")
        return

    per_page = 10
    pages = [resultados[i:i+per_page] for i in range(0, len(resultados), per_page)]

    class RankingView(View):
        def __init__(self):
            super().__init__(timeout=None)
            self.page = 0

        @discord.ui.button(label="⬅️", style=discord.ButtonStyle.gray)
        async def previous(self, button: Button, interaction):
            if self.page > 0:
                self.page -= 1
                await interaction.response.edit_message(content=self.format_page(), view=self)

        @discord.ui.button(label="➡️", style=discord.ButtonStyle.gray)
        async def next(self, button: Button, interaction):
            if self.page < len(pages) - 1:
                self.page += 1
                await interaction.response.edit_message(content=self.format_page(), view=self)

        def format_page(self):
            msg = f"**🏆 Ranking de Pontos (Página {self.page+1}/{len(pages)}):**\n"
            for i, (user_id, pontos) in enumerate(pages[self.page], start=self.page*per_page+1):
                membro = ctx.guild.get_member(user_id)
                nome = membro.display_name if membro else "Usuário desconhecido"
                msg += f"{i}. {nome} — {pontos} pontos\n"
            return msg

    view = RankingView()
    # Mostra sempre o Top 10 na primeira página
    await ctx.send(content=view.format_page(), view=view)

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
