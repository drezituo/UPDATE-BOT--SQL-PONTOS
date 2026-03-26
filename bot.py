import discord
from discord.ext import commands
import psycopg2

# ---------- CONFIGURAÇÃO ----------
TOKEN = "SEU_TOKEN_AQUI"
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- CONEXÃO COM O BANCO ----------
def get_connection():
    return psycopg2.connect(
        host="SEU_HOST",
        database="SEU_DB",
        user="SEU_USER",
        password="SUA_SENHA",
        sslmode="require"
    )

# ---------- COMANDOS DE PONTOS ----------

@bot.command()
async def pontos(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0

    cursor.close()
    conn.close()
    await ctx.send(f"{membro.mention} tem **{pontos} presenças**!")

@bot.command()
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    if r:
        cursor.execute("UPDATE pontos SET pontos = pontos + %s WHERE user_id = %s", (quantidade, membro.id))
    else:
        cursor.execute("INSERT INTO pontos (user_id, pontos) VALUES (%s, %s)", (membro.id, quantidade))
    conn.commit()
    cursor.close()
    conn.close()
    await ctx.send(f"{quantidade} ponto(s) adicionados a {membro.mention}!")

@bot.command()
async def removepontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    if r:
        novo_valor = max(0, r[0] - quantidade)
        cursor.execute("UPDATE pontos SET pontos = %s WHERE user_id = %s", (novo_valor, membro.id))
        conn.commit()
    cursor.close()
    conn.close()
    await ctx.send(f"{quantidade} ponto(s) removido(s) de {membro.mention}!")

# ---------- PONTOS SOLO ----------

@bot.command()
async def pontossolo(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    await ctx.send(f"{membro.mention} tem **{pontos} vitórias no Solo Rebirth!**")
    cursor.close()
    conn.close()

@bot.command()
async def removesolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    if r:
        novo_valor = max(0, r[0] - quantidade)
        cursor.execute("UPDATE pontos_solo SET pontos = %s WHERE user_id = %s", (novo_valor, membro.id))
        conn.commit()
    cursor.close()
    conn.close()
    await ctx.send(f"{quantidade} vitória(s) Solo removida(s) de {membro.mention}!")

@bot.command()
async def pontosteam(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    await ctx.send(f"{membro.mention} tem **{pontos} vitórias no Team Rebirth!**")
    cursor.close()
    conn.close()

@bot.command()
async def removeteam(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    if r:
        novo_valor = max(0, r[0] - quantidade)
        cursor.execute("UPDATE pontos_team SET pontos = %s WHERE user_id = %s", (novo_valor, membro.id))
        conn.commit()
    cursor.close()
    conn.close()
    await ctx.send(f"{quantidade} vitória(s) Team removida(s) de {membro.mention}!")

# ---------- RANKINGS ----------

@bot.command()
async def ranking(ctx):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    resultado = cursor.fetchall()
    cursor.close()
    conn.close()

    mensagem = "**🏆 Ranking de Presenças:**\n"
    for i, (uid, pts) in enumerate(resultado, 1):
        user = bot.get_user(uid)
        nome = user.display_name if user else "None"
        mensagem += f"{i}. {nome} — {pts} pontos\n"
    await ctx.send(mensagem)

@bot.command()
async def rankingsolo(ctx):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, pontos FROM pontos_solo ORDER BY pontos DESC")
    resultado = cursor.fetchall()
    cursor.close()
    conn.close()

    mensagem = "**🎯 Ranking Solo Rebirth:**\n"
    for i, (uid, pts) in enumerate(resultado, 1):
        user = bot.get_user(uid)
        nome = user.display_name if user else "None"
        mensagem += f"{i}. {nome} — {pts} vitórias\n"
    await ctx.send(mensagem)

@bot.command()
async def rankingteam(ctx):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, pontos FROM pontos_team ORDER BY pontos DESC")
    resultado = cursor.fetchall()
    cursor.close()
    conn.close()

    mensagem = "**👥 Ranking Team Rebirth:**\n"
    for i, (uid, pts) in enumerate(resultado, 1):
        user = bot.get_user(uid)
        nome = user.display_name if user else "None"
        mensagem += f"{i}. {nome} — {pts} vitórias\n"
    await ctx.send(mensagem)

# ---------- STATUS ----------

@bot.command()
async def status(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    conn = get_connection()
    cursor = conn.cursor()

    # pontos normais
    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    cursor.execute("SELECT user_id FROM pontos ORDER BY pontos DESC")
    ranking_pontos = [u[0] for u in cursor.fetchall()]
    pos_pontos = ranking_pontos.index(membro.id) + 1 if membro.id in ranking_pontos else "—"

    # solo
    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    solo = r[0] if r else 0
    cursor.execute("SELECT user_id FROM pontos_solo ORDER BY pontos DESC")
    ranking_solo = [u[0] for u in cursor.fetchall()]
    pos_solo = ranking_solo.index(membro.id) + 1 if membro.id in ranking_solo else "—"

    # team
    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    team = r[0] if r else 0
    cursor.execute("SELECT user_id FROM pontos_team ORDER BY pontos DESC")
    ranking_team = [u[0] for u in cursor.fetchall()]
    pos_team = ranking_team.index(membro.id) + 1 if membro.id in ranking_team else "—"

    cursor.close()
    conn.close()

    embed = discord.Embed(
        title=f"📊 Status de {membro.display_name}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=membro.display_avatar.url)
    embed.add_field(name="⭐ Presenças", value=f"**{pontos}** presenças\n🏆 Posição: **{pos_pontos}º**", inline=False)
    embed.add_field(name="🎯 Solo Rebirth", value=f"**{solo}** vitórias solo\n🏆 Posição: **{pos_solo}º**", inline=False)
    embed.add_field(name="👥 Team Rebirth", value=f"**{team}** vitórias team\n🏆 Posição: **{pos_team}º**", inline=False)
    embed.set_footer(text="StunhouseCQB Points System")
    await ctx.send(embed=embed)

# ---------- INICIALIZAÇÃO ----------
@bot.event
async def on_ready():
    print(f"✅ Bot ligado como {bot.user}")

bot.run(TOKEN)
