import discord
from discord.ext import commands
import os
import psycopg2
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO

# ---------- INTENTS ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- DATABASE ----------
def get_db():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

def setup_database():
    conn = get_db()
    cursor = conn.cursor()

    # Pontos normal (NÃO ALTERAR - mantém dados)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pontos (
        user_id BIGINT PRIMARY KEY,
        nome TEXT,
        pontos INTEGER
    )
    """)

    # Rebirth
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pontos_rebirth (
        user_id BIGINT PRIMARY KEY,
        pontos_solo INTEGER DEFAULT 0,
        pontos_team INTEGER DEFAULT 0
    )
    """)

    # Perfil
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS perfil (
        user_id BIGINT PRIMARY KEY,
        replica TEXT,
        modo TEXT,
        solowin INTEGER DEFAULT 0,
        teamwin INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    cursor.close()
    conn.close()

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    setup_database()
    print(f"✅ Bot ligado como {bot.user}")

# ---------- PONTOS NORMAL ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def addpontos(ctx, membro: discord.Member, quantidade: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos WHERE user_id = %s", (membro.id,))
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
    cursor.close()
    conn.close()

    await ctx.send(f"✅ {membro.mention} agora tem **{novo_total} pontos**")

@bot.command()
async def ranking(ctx):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT nome, pontos FROM pontos ORDER BY pontos DESC")
    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Ainda não há pontos.")
        return

    mensagem = "🏆 Ranking de Pontos:\n"

    for i, (nome, pontos) in enumerate(resultados, start=1):
        mensagem += f"{i}. {nome} — {pontos}\n"

    for chunk in [mensagem[i:i+1900] for i in range(0, len(mensagem), 1900)]:
        await ctx.send(chunk)

    cursor.close()
    conn.close()

# ---------- REBIRTH ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def addrebirth(ctx, membro: discord.Member, tipo: str, quantidade: int):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM pontos_rebirth WHERE user_id = %s", (membro.id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO pontos_rebirth (user_id) VALUES (%s)", (membro.id,))

    if tipo.lower() == "solo":
        cursor.execute(
            "UPDATE pontos_rebirth SET pontos_solo = pontos_solo + %s WHERE user_id = %s",
            (quantidade, membro.id)
        )
    elif tipo.lower() == "team":
        cursor.execute(
            "UPDATE pontos_rebirth SET pontos_team = pontos_team + %s WHERE user_id = %s",
            (quantidade, membro.id)
        )
    else:
        await ctx.send("❌ Usa: solo ou team")
        return

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"🔥 {membro.mention} recebeu {quantidade} pontos ({tipo})")

# ---------- RANKING REBIRTH ----------
@bot.command()
async def rankingrebirth(ctx, tipo: str):
    conn = get_db()
    cursor = conn.cursor()

    if tipo.lower() == "solo":
        cursor.execute("SELECT user_id, pontos_solo FROM pontos_rebirth ORDER BY pontos_solo DESC")
    else:
        cursor.execute("SELECT user_id, pontos_team FROM pontos_rebirth ORDER BY pontos_team DESC")

    resultados = cursor.fetchall()

    if not resultados:
        await ctx.send("⚠️ Sem dados.")
        return

    mensagem = f"🏆 Ranking Rebirth ({tipo}):\n"

    for i, (user_id, pontos) in enumerate(resultados, start=1):
        membro = ctx.guild.get_member(user_id)
        nome = membro.display_name if membro else "Desconhecido"
        mensagem += f"{i}. {nome} — {pontos}\n"

    for chunk in [mensagem[i:i+1900] for i in range(0, len(mensagem), 1900)]:
        await ctx.send(chunk)

    cursor.close()
    conn.close()

# ---------- PERFIL ----------
@bot.command()
async def setperfil(ctx, replica: str, modo: str):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM perfil WHERE user_id = %s", (ctx.author.id,))
    if cursor.fetchone():
        cursor.execute(
            "UPDATE perfil SET replica=%s, modo=%s WHERE user_id=%s",
            (replica, modo, ctx.author.id)
        )
    else:
        cursor.execute(
            "INSERT INTO perfil (user_id, replica, modo) VALUES (%s, %s, %s)",
            (ctx.author.id, replica, modo)
        )

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send("✅ Perfil atualizado!")

# ---------- VITÓRIAS ----------
@bot.command()
@commands.has_permissions(administrator=True)
async def addwin(ctx, membro: discord.Member, tipo: str):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM perfil WHERE user_id = %s", (membro.id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO perfil (user_id) VALUES (%s)", (membro.id,))

    if tipo.lower() == "solowin":
        cursor.execute("UPDATE perfil SET solowin = solowin + 1 WHERE user_id = %s", (membro.id,))
    elif tipo.lower() == "teamwin":
        cursor.execute("UPDATE perfil SET teamwin = teamwin + 1 WHERE user_id = %s", (membro.id,))
    else:
        await ctx.send("❌ Usa: solowin ou teamwin")
        return

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"🏆 Vitória adicionada a {membro.mention}")

# ---------- PERFIL IMAGEM ----------
@bot.command()
async def perfil(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM perfil WHERE user_id = %s", (membro.id,))
    dados = cursor.fetchone()

    if not dados:
        await ctx.send("⚠️ Perfil não criado.")
        return

    _, replica, modo, solowin, teamwin = dados

    # Criar imagem
    img = Image.new("RGB", (800, 300), (25, 25, 25))
    draw = ImageDraw.Draw(img)

    font = ImageFont.load_default()

    # Avatar
    avatar_url = membro.avatar.url if membro.avatar else membro.default_avatar.url
    avatar = Image.open(BytesIO(requests.get(avatar_url).content)).resize((150, 150))
    img.paste(avatar, (20, 75))

    # Texto
    draw.text((200, 40), membro.display_name, font=font, fill=(255,255,255))
    draw.text((200, 90), f"🔫 {replica}", font=font, fill=(200,200,200))
    draw.text((200, 120), f"🎯 {modo}", font=font, fill=(200,200,200))
    draw.text((200, 170), f"🏆 SoloWin: {solowin}", font=font, fill=(255,215,0))
    draw.text((200, 200), f"🤝 TeamWin: {teamwin}", font=font, fill=(100,200,255))

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    await ctx.send(file=discord.File(buffer, "perfil.png"))

    cursor.close()
    conn.close()

# ---------- RUN ----------
bot.run(os.getenv("DISCORD_TOKEN"))
