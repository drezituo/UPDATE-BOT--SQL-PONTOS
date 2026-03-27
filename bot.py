import discord
from discord.ext import commands
import psycopg2
import os
import asyncio

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

DB_ERROR_MSG = "✅ Bot ativado, volta a digitar o comando."

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
def get_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"Erro ao ligar à DB: {e}")
        return None

# ---------- CRIAR TABELAS ----------
conn = get_connection()
if conn:
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pontos (
        user_id BIGINT PRIMARY KEY,
        pontos INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pontos_solo (
        user_id BIGINT PRIMARY KEY,
        pontos INTEGER DEFAULT 0
    )
    """)

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

@bot.event
async def on_command_error(ctx, error):
    print(f"Erro: {error}")

# =========================
# 🔥 SISTEMA ANTIGO
# =========================

@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    resultado = cursor.fetchone()

    total = resultado[0] if resultado else 0

    cursor.close()
    conn.close()

    await ctx.send(f"⭐ {membro.display_name} tem **{total} pontos**")

# =========================
# 🆕 RANKING PONTOS NORMAL
# =========================
@bot.command()
async def ranking(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos ORDER BY pontos DESC")
    dados = cursor.fetchall()

    if not dados:
        await ctx.send("⚠️ Nenhum ponto registrado ainda.")
        cursor.close()
        conn.close()
        return

    msg = "**🏆 Ranking de Pontos:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        membro = ctx.guild.get_member(uid)
        nome = membro.display_name if membro else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} pontos\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()

# =========================
# 🆕 SOLO REBIRTH
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addsolo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
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

# =========================
# 🆕 STATUS (COM TIERS)
# =========================
@bot.command()
async def status(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    # ---------- PONTOS NORMAIS ----------
    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos ORDER BY pontos DESC")
    ranking_dados = [uid for (uid,) in cursor.fetchall()]
    rank_pontos = ranking_dados.index(membro.id) + 1 if membro.id in ranking_dados else "N/A"

    # ---------- SOLO ----------
    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_solo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_solo ORDER BY pontos DESC")
    ranking_dados_solo = [uid for (uid,) in cursor.fetchall()]
    rank_solo = ranking_dados_solo.index(membro.id) + 1 if membro.id in ranking_dados_solo else "N/A"

    # ---------- TEAM ----------
    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_team = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_team ORDER BY pontos DESC")
    ranking_dados_team = [uid for (uid,) in cursor.fetchall()]
    rank_team = ranking_dados_team.index(membro.id) + 1 if membro.id in ranking_dados_team else "N/A"

    cursor.close()
    conn.close()

    def get_tier_data(valor):
        if valor <= 10:
            tier = 1
        elif valor <= 20:
            tier = 2
        elif valor <= 30:
            tier = 3
        elif valor <= 40:
            tier = 4
        else:
            tier = 5

        if tier == 1:
            progresso = valor
        elif tier == 2:
            progresso = valor - 10
        elif tier == 3:
            progresso = valor - 20
        elif tier == 4:
            progresso = valor - 30
        else:
            progresso = valor - 40

        if progresso > 10:
            progresso = 10
        if progresso < 0:
            progresso = 0

        emojis_tier = {
            1: ("🟩", "⬛", "BRONZE"),
            2: ("🟦", "⬛", "PRATA"),
            3: ("🟨", "⬛", "OURO"),
            4: ("🟧", "⬛", "PLATINA"),
            5: ("🟥", "⬛", "DIAMANTE"),
        }

        cheio, vazio, nome_tier = emojis_tier[tier]
        barra = cheio * progresso + vazio * (10 - progresso)

        return tier, nome_tier, barra, progresso

    tier_pontos, nome_tier_pontos, barra_pontos, prog_pontos = get_tier_data(pontos)
    tier_solo, nome_tier_solo, barra_solo, prog_solo = get_tier_data(pontos_solo)
    tier_team, nome_tier_team, barra_team, prog_team = get_tier_data(pontos_team)

    embed = discord.Embed(
        title="🎮┃PERFIL DE COMBATE",
        description=f"**{membro.display_name}**",
        color=0x2b2d31
    )

    embed.set_thumbnail(url=membro.display_avatar.url)

    embed.add_field(
        name="⭐ PRESENÇAS",
        value=(
            f"**Tier {tier_pontos} • {nome_tier_pontos}**\n"
            f"{barra_pontos}\n"
            f"`{prog_pontos}/10` no tier atual • 🏅 `#{rank_pontos}`\n"
            f"Total: **{pontos:02} presenças**"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 SOLO REBIRTH",
        value=(
            f"**Tier {tier_solo} • {nome_tier_solo}**\n"
            f"{barra_solo}\n"
            f"`{prog_solo}/10` no tier atual • 🏅 `#{rank_solo}`\n"
            f"Total: **{pontos_solo:02} vitórias**"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 TEAM REBIRTH",
        value=(
            f"**Tier {tier_team} • {nome_tier_team}**\n"
            f"{barra_team}\n"
            f"`{prog_team}/10` no tier atual • 🏅 `#{rank_team}`\n"
            f"Total: **{pontos_team:02} vitórias**"
        ),
        inline=False
    )

    embed.add_field(
        name="🏆 RESUMO",
        value=(
            f"🎯 Total vitórias: **{pontos_solo + pontos_team}**\n"
            f"📊 Participações: **{pontos}**"
        ),
        inline=False
    )

    embed.set_footer(text="⚡ Sistema competitivo ativo • 5 Tiers")
    embed.timestamp = discord.utils.utcnow()

    await ctx.send(embed=embed)

# ---------- AUTO RESTART ----------
async def start_bot():
    while True:
        try:
            await bot.start(TOKEN)
        except Exception as e:
            print(f"Erro crítico: {e}")
            await asyncio.sleep(5)

asyncio.run(start_bot())
