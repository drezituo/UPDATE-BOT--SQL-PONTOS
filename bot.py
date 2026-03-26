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

# ---------- DATABASE ----------
# ---------- DATABASE (POSTGRESQL - RAILWAY) ----------
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()
@@ -68,50 +68,31 @@ async def pontos(ctx, membro: discord.Member = None):
    total = resultado[0] if resultado else 0
    await ctx.send(f"⭐ {membro.mention} tem **{total} pontos**")

# ---------- PAGINATED RANKING ----------
# ---------- RANKING ----------
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
        def __init__(self, guild):
            super().__init__(timeout=None)
            self.guild = guild
            self.page = 0

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            # Permitir todos a interagir
            return True

        def format_page(self):
            msg = f"**🏆 Ranking de Pontos (Página {self.page+1}/{len(pages)}):**\n"
            for i, (user_id, pontos) in enumerate(pages[self.page], start=self.page*per_page+1):
                membro = self.guild.get_member(user_id)
                nome = membro.display_name if membro else "Usuário desconhecido"
                msg += f"{i}. {nome} — {pontos} pontos\n"
            return msg

        @discord.ui.button(label="⬅️", style=discord.ButtonStyle.gray)
        async def previous(self, button: Button, interaction: discord.Interaction):
            if self.page > 0:
                self.page -= 1
                await interaction.response.edit_message(content=self.format_page(), view=self)

        @discord.ui.button(label="➡️", style=discord.ButtonStyle.gray)
        async def next(self, button: Button, interaction: discord.Interaction):
            if self.page < len(pages) - 1:
                self.page += 1
                await interaction.response.edit_message(content=self.format_page(), view=self)

    view = RankingView(ctx.guild)
    await ctx.send(content=view.format_page(), view=view)
    # Construir mensagens respeitando limite de 2000 caracteres
    per_message = 2000
    mensagem = "**🏆 Ranking de Pontos:**\n"
    for i, (user_id, pontos) in enumerate(resultados, start=1):
        membro = ctx.guild.get_member(user_id)
        nome = membro.display_name if membro else "Usuário desconhecido"
        linha = f"{i}. {nome} — {pontos} pontos\n"

        if len(mensagem) + len(linha) > per_message:
            await ctx.send(mensagem)
            mensagem = ""
        mensagem += linha

    if mensagem:
        await ctx.send(mensagem)

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
