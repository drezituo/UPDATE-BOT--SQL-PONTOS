import discord
from discord.ext import commands, tasks
import psycopg2
import os
from datetime import datetime, timezone, timedelta

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

DB_ERROR_MSG = "✅ Bot ativado, volta a digitar o comando."

# ---------- INSCRIÇÕES ----------
LIMITE_PADRAO_INSCRICOES = 25
EMOJI_CONFIRMACAO = "✅"
HORAS_PAGAMENTO = 12
MINUTOS_AVISO = 30

# ---------- ROLE IDS (TIERS POR PONTOS NORMAIS) ----------
TIER_1_ROLE_ID = 1458650693316509718
TIER_2_ROLE_ID = 1463719829247885404
TIER_3_ROLE_ID = 1463723971068301446
TIER_4_ROLE_ID = 1463720049700372563
TIER_5_ROLE_ID = 1487161001752400074

TIER_ROLE_IDS = [
    TIER_1_ROLE_ID,
    TIER_2_ROLE_ID,
    TIER_3_ROLE_ID,
    TIER_4_ROLE_ID,
    TIER_5_ROLE_ID,
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- DATABASE ----------
def get_connection():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"Erro ao ligar à DB: {e}")
        return None


# ---------- TIER HELPERS ----------
def get_tier_from_points(valor: int) -> int:
    if valor <= 10:
        return 1
    elif valor <= 20:
        return 2
    elif valor <= 30:
        return 3
    elif valor <= 40:
        return 4
    else:
        return 5


def get_tier_role_id(tier: int) -> int:
    tier_map = {
        1: TIER_1_ROLE_ID,
        2: TIER_2_ROLE_ID,
        3: TIER_3_ROLE_ID,
        4: TIER_4_ROLE_ID,
        5: TIER_5_ROLE_ID,
    }
    return tier_map[tier]


async def update_member_tier_role(membro: discord.Member, pontos_normais: int):
    tier = get_tier_from_points(pontos_normais)
    correct_role_id = get_tier_role_id(tier)

    roles_to_remove = [role for role in membro.roles if role.id in TIER_ROLE_IDS and role.id != correct_role_id]
    if roles_to_remove:
        await membro.remove_roles(*roles_to_remove, reason="Atualização automática de tier por pontos")

    correct_role = membro.guild.get_role(correct_role_id)
    if correct_role and correct_role not in membro.roles:
        await membro.add_roles(correct_role, reason="Atualização automática de tier por pontos")


# ---------- ACTIVITY HELPERS ----------
def formatar_inatividade(dt):
    if not dt:
        return "Sem atividade registada"

    agora = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = agora - dt
    dias = diff.days
    segundos = diff.seconds
    horas = segundos // 3600
    minutos = (segundos % 3600) // 60

    if dias > 0:
        return f"{dias} dia(s)"
    if horas > 0:
        return f"{horas} hora(s)"
    return f"{minutos} minuto(s)"


# ---------- INSCRIÇÕES HELPERS ----------
def utc_now():
    return datetime.now(timezone.utc)


def is_thread_channel(channel):
    return isinstance(channel, discord.Thread)


def normalizar_nome(nome: str) -> str:
    return " ".join(nome.split()).strip()


def encontrar_membro_por_nome(guild: discord.Guild, nome: str):
    if guild is None:
        return None

    nome_normalizado = normalizar_nome(nome).lower()

    for member in guild.members:
        candidatos = {
            normalizar_nome(member.display_name).lower(),
            normalizar_nome(member.name).lower(),
        }

        global_name = getattr(member, "global_name", None)
        if global_name:
            candidatos.add(normalizar_nome(global_name).lower())

        if nome_normalizado in candidatos:
            return member

    return None


def obter_avatar_url_jogador(guild: discord.Guild, nome_jogador: str):
    membro = encontrar_membro_por_nome(guild, nome_jogador)

    if membro:
        return membro.display_avatar.url

    return "https://cdn.discordapp.com/embed/avatars/0.png"


async def apagar_mensagem_comando(ctx):
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


async def utilizador_e_admin_no_guild(guild: discord.Guild, user_id: int) -> bool:
    if guild is None:
        return False

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    return member.guild_permissions.administrator


def obter_thread_config(thread_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT inscricoes_abertas, limite, thread_name, estado_msg_id
        FROM jogos_threads
        WHERE thread_id = %s
    """, (thread_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def contar_inscricoes_validas(thread_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (thread_id,))
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total


async def criar_embed_inscricao_pendente(ctx, nome: str, expira_em: datetime):
    avatar_url = obter_avatar_url_jogador(ctx.guild, nome)

    embed = discord.Embed(
        title=f"🎟️ Inscrição - {ctx.channel.name}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=ctx.author.mention, inline=True)
    embed.add_field(name="📌 Estado", value="⏳ Inscrição por finalizar", inline=False)
    embed.add_field(name="⏰ Expira", value=f"<t:{int(expira_em.timestamp())}:F>", inline=True)
    embed.add_field(name="⌛ Tempo restante", value=f"<t:{int(expira_em.timestamp())}:R>", inline=True)
    embed.set_footer(text=f"⚡ Tens {HORAS_PAGAMENTO} horas para concluir a inscrição")
    return embed


async def criar_embed_inscricao_paga(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str):
    avatar_url = obter_avatar_url_jogador(guild, nome)

    embed = discord.Embed(
        title=f"🎟️ Inscrição confirmada - {thread_name}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="✅ Inscrição finalizada", inline=False)
    embed.set_footer(text="✅ Inscrição concluída com sucesso")
    return embed


async def criar_embed_inscricao_expirada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str):
    avatar_url = obter_avatar_url_jogador(guild, nome)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="❌ Inscrição expirada por falta de finalização", inline=False)
    embed.set_footer(text="⌛ O prazo para finalizar a inscrição terminou")
    return embed


async def criar_embed_inscricao_cancelada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, cancelado_por=""):
    avatar_url = obter_avatar_url_jogador(guild, nome)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=nome, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="❌ Inscrição cancelada manualmente", inline=False)
    embed.add_field(name="🛑 Cancelado por", value=cancelado_por, inline=False)
    embed.set_footer(text="ℹ️ A vaga foi libertada")
    return embed


async def atualizar_embed_estado(channel: discord.Thread):
    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT inscricoes_abertas, limite, estado_msg_id
        FROM jogos_threads
        WHERE thread_id = %s
    """, (channel.id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return

    abertas, limite, estado_msg_id = row

    cursor.execute("""
        SELECT COUNT(*)
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND estado IN ('pendente_pagamento', 'pago')
    """, (channel.id,))
    total = cursor.fetchone()[0]

    embed = discord.Embed(
        title="📋 Estado das Inscrições",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="🔓 Estado", value="Abertas" if abertas else "Fechadas", inline=True)
    embed.add_field(name="✅ Inscrições ativas", value=str(total), inline=True)
    embed.add_field(name="📉 Vagas restantes", value=str(max(limite - total, 0)), inline=True)

    if estado_msg_id:
        try:
            old_msg = await channel.fetch_message(estado_msg_id)
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        new_msg = await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        cursor.close()
        conn.close()
        return

    cursor.execute("""
        UPDATE jogos_threads
        SET estado_msg_id = %s
        WHERE thread_id = %s
    """, (new_msg.id, channel.id))
    conn.commit()
    cursor.close()
    conn.close()


# ---------- CRIAR TABELAS ----------
conn = get_connection()
if conn:
    cursor = None
    try:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        ALTER TABLE pontos
        ADD COLUMN IF NOT EXISTS ultima_atividade TIMESTAMP
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

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pontos_tempo (
            user_id BIGINT PRIMARY KEY,
            pontos INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jogos_threads (
            thread_id BIGINT PRIMARY KEY,
            thread_name TEXT NOT NULL,
            inscricoes_abertas BOOLEAN NOT NULL DEFAULT FALSE,
            limite INTEGER NOT NULL DEFAULT 25,
            criado_por BIGINT NOT NULL,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            estado_msg_id BIGINT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS inscricoes_jogos (
            id BIGSERIAL PRIMARY KEY,
            thread_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            nome_jogador TEXT NOT NULL,
            estado TEXT NOT NULL CHECK (estado IN ('pendente_pagamento', 'pago', 'expirado', 'cancelado')),
            criado_em TIMESTAMPTZ NOT NULL,
            expira_em TIMESTAMPTZ NOT NULL,
            aviso_30min_enviado BOOLEAN NOT NULL DEFAULT FALSE,
            confirmado_por BIGINT,
            confirmado_em TIMESTAMPTZ
        )
        """)

        cursor.execute("""
        ALTER TABLE jogos_threads
        ADD COLUMN IF NOT EXISTS estado_msg_id BIGINT
        """)

        cursor.execute("""
        ALTER TABLE inscricoes_jogos
        DROP CONSTRAINT IF EXISTS inscricoes_jogos_thread_id_user_id_key
        """)

        conn.commit()
        print("✅ Tabelas verificadas/criadas com sucesso.")

    except Exception as e:
        conn.rollback()
        print(f"❌ Erro ao criar tabelas: {e}")

    finally:
        if cursor:
            cursor.close()
        conn.close()
else:
    print("❌ Não foi possível ligar à base de dados para criar/verificar as tabelas.")


# ---------- EVENT ----------
@bot.event
async def on_ready():
    print("========== BOT READY ==========")
    print(f"✅ Bot ligado como {bot.user}")
    print(f"message_content: {bot.intents.message_content}")
    print(f"members: {bot.intents.members}")
    print(f"guilds: {bot.intents.guilds}")
    print("===============================")

    if not verificar_inscricoes.is_running():
        verificar_inscricoes.start()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    print(f"[MSG] {message.author} em #{message.channel}: {message.content}")
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Não tens permissão para usar este comando.", delete_after=10)
        return

    if isinstance(error, commands.MissingRequiredArgument):
        if ctx.command and ctx.command.name in ("inscrever", "cancelarinscricao"):
            await ctx.send("⚠️ Usa o comando com o nome do jogador.", delete_after=10)
            return

    print(f"Erro completo: {repr(error)}")
    try:
        await ctx.send(f"Erro: `{repr(error)}`", delete_after=15)
    except Exception:
        pass


# =========================
# 🆕 INSCRIÇÕES EM THREADS
# =========================
@bot.command()
async def ping(ctx):
    await ctx.send("pong")


@bot.command()
@commands.has_permissions(administrator=True)
async def abrir_inscricoes(ctx, limite: int = LIMITE_PADRAO_INSCRICOES):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    if limite <= 0:
        return await ctx.send("⚠️ O limite tem de ser superior a 0.", delete_after=10)

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jogos_threads (thread_id, thread_name, inscricoes_abertas, limite, criado_por)
        VALUES (%s, %s, TRUE, %s, %s)
        ON CONFLICT (thread_id)
        DO UPDATE SET
            thread_name = EXCLUDED.thread_name,
            inscricoes_abertas = TRUE,
            limite = EXCLUDED.limite,
            criado_por = EXCLUDED.criado_por
    """, (ctx.channel.id, ctx.channel.name, limite, ctx.author.id))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"✅ Inscrições abertas nesta thread.\n👥 Limite: **{limite} jogadores**")
    await atualizar_embed_estado(ctx.channel)


@bot.command()
@commands.has_permissions(administrator=True)
async def fechar_inscricoes(ctx):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        UPDATE jogos_threads
        SET inscricoes_abertas = FALSE
        WHERE thread_id = %s
    """, (ctx.channel.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send("🛑 Inscrições fechadas nesta thread.")
    await atualizar_embed_estado(ctx.channel)


@bot.command()
@commands.has_permissions(administrator=True)
async def estado_inscricoes(ctx):
    if not is_thread_channel(ctx.channel):
        return await ctx.send("⚠️ Este comando só pode ser usado dentro de uma thread.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        return await ctx.send("ℹ️ Esta thread ainda não foi configurada para inscrições.", delete_after=10)

    await atualizar_embed_estado(ctx.channel)


@bot.command()
async def inscrever(ctx, *, nome: str):
    nome = normalizar_nome(nome)

    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    if len(nome) < 3:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Nome inválido.", delete_after=10)

    dados = obter_thread_config(ctx.channel.id)
    if not dados:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ As inscrições não estão ativas nesta thread.", delete_after=10)

    abertas, limite, _, _ = dados

    if not abertas:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("🛑 As inscrições estão fechadas nesta thread.", delete_after=10)

    validas = contar_inscricoes_validas(ctx.channel.id)
    if validas is None:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    if validas >= limite:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(f"⚠️ Este jogo já atingiu o limite de **{limite} jogadores**.", delete_after=10)

    conn = get_connection()
    if not conn:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT 1
        FROM inscricoes_jogos
        WHERE thread_id = %s
          AND LOWER(nome_jogador) = LOWER(%s)
          AND estado IN ('pendente_pagamento', 'pago')
    """, (ctx.channel.id, nome))
    existe_nome = cursor.fetchone()

    if existe_nome:
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Já existe uma inscrição com esse nome neste jogo.", delete_after=10)

    criado_em = utc_now()
    expira_em = criado_em + timedelta(hours=HORAS_PAGAMENTO)

    embed = await criar_embed_inscricao_pendente(ctx, nome, expira_em)
    msg = await ctx.channel.send(embed=embed)

    try:
        cursor.execute("""
            INSERT INTO inscricoes_jogos (
                thread_id, message_id, user_id, nome_jogador,
                estado, criado_em, expira_em, aviso_30min_enviado
            )
            VALUES (%s, %s, %s, %s, 'pendente_pagamento', %s, %s, FALSE)
        """, (
            ctx.channel.id,
            msg.id,
            ctx.author.id,
            nome,
            criado_em,
            expira_em
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        print(f"Erro ao inserir inscrição: {e}")
        return await ctx.send("⚠️ Erro ao criar a inscrição.", delete_after=10)

    cursor.close()
    conn.close()
    await apagar_mensagem_comando(ctx)
    await atualizar_embed_estado(ctx.channel)


@bot.command()
async def cancelarinscricao(ctx, *, nome: str):
    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    nome = normalizar_nome(nome)
    if len(nome) < 3:
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Nome inválido.", delete_after=10)

    is_admin = ctx.author.guild_permissions.administrator

    conn = get_connection()
    if not conn:
        await apagar_mensagem_comando(ctx)
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    if is_admin:
        cursor.execute("""
            SELECT id, message_id, nome_jogador, user_id
            FROM inscricoes_jogos
            WHERE thread_id = %s
              AND LOWER(nome_jogador) = LOWER(%s)
              AND estado IN ('pendente_pagamento', 'pago')
            ORDER BY id DESC
            LIMIT 1
        """, (ctx.channel.id, nome))
    else:
        cursor.execute("""
            SELECT id, message_id, nome_jogador, user_id
            FROM inscricoes_jogos
            WHERE thread_id = %s
              AND LOWER(nome_jogador) = LOWER(%s)
              AND user_id = %s
              AND estado IN ('pendente_pagamento', 'pago')
            ORDER BY id DESC
            LIMIT 1
        """, (ctx.channel.id, nome, ctx.author.id))

    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        await apagar_mensagem_comando(ctx)
        if is_admin:
            return await ctx.send("⚠️ Não existe nenhuma inscrição ativa com esse nome nesta thread.", delete_after=10)
        return await ctx.send("⚠️ Não encontrei uma inscrição ativa tua com esse nome nesta thread.", delete_after=10)

    inscricao_id, message_id, nome_jogador, user_id = row

    cursor.execute("""
        UPDATE inscricoes_jogos
        SET estado = 'cancelado'
        WHERE id = %s
    """, (inscricao_id,))
    conn.commit()
    cursor.close()
    conn.close()

    user = ctx.guild.get_member(user_id)
    if user is None:
        try:
            user = await ctx.guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None

    autor_inscricao_mention = f"<@{user_id}>"

    if ctx.author.id == user_id:
        cancelado_por = ctx.author.mention
    else:
        cancelado_por = f"{ctx.author.mention} (admin)"

    try:
        msg = await ctx.channel.fetch_message(message_id)
        embed = await criar_embed_inscricao_cancelada(
            ctx.guild,
            ctx.channel.name,
            nome_jogador,
            autor_inscricao_mention,
            cancelado_por
        )
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    await apagar_mensagem_comando(ctx)

    if ctx.author.id == user_id:
        await ctx.send(f"✅ A inscrição de **{nome_jogador}** foi cancelada.", delete_after=10)
    else:
        await ctx.send(f"✅ A inscrição de **{nome_jogador}** foi cancelada por {ctx.author.mention}.", delete_after=10)

    if user and ctx.author.id != user_id:
        try:
            await user.send(f"❌ A inscrição de **{nome_jogador}** na thread **{ctx.channel.name}** foi cancelada por um admin.")
        except (discord.Forbidden, discord.HTTPException):
            pass

    await atualizar_embed_estado(ctx.channel)


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
        cursor.execute(
            "UPDATE pontos SET pontos = %s, ultima_atividade = NOW() WHERE user_id = %s",
            (novo_total, membro.id)
        )
    else:
        novo_total = quantidade
        cursor.execute(
            "INSERT INTO pontos (user_id, pontos, ultima_atividade) VALUES (%s, %s, NOW())",
            (membro.id, quantidade)
        )

    conn.commit()
    cursor.close()
    conn.close()

    try:
        await update_member_tier_role(membro, novo_total)
    except Exception as e:
        print(f"Erro ao atualizar cargo de tier: {e}")

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
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Esse usuário não tem pontos.")

    novo_total = max(resultado[0] - quantidade, 0)
    cursor.execute("UPDATE pontos SET pontos = %s WHERE user_id = %s", (novo_total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    try:
        await update_member_tier_role(membro, novo_total)
    except Exception as e:
        print(f"Erro ao atualizar cargo de tier: {e}")

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
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

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
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

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
# 🆕 TEMPO EM PISTA
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def addtempo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = (r[0] if r else 0) + quantidade

    if r:
        cursor.execute("UPDATE pontos_tempo SET pontos = %s WHERE user_id = %s", (total, membro.id))
    else:
        cursor.execute("INSERT INTO pontos_tempo VALUES (%s, %s)", (membro.id, total))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"⏱️ {membro.display_name} agora tem **{total} tempo em pista**")


@bot.command()
@commands.has_permissions(administrator=True)
async def removetempo(ctx, membro: discord.Member, quantidade: int):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    if not r:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Sem dados.")

    total = max(r[0] - quantidade, 0)

    cursor.execute("UPDATE pontos_tempo SET pontos = %s WHERE user_id = %s", (total, membro.id))

    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} tempo em pista**")


@bot.command()
async def tempopista(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()

    total = r[0] if r else 0

    cursor.close()
    conn.close()

    await ctx.send(f"⏱️ {membro.display_name} tem **{total} tempo em pista**")


@bot.command()
async def rankingtempo(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_tempo ORDER BY pontos DESC")
    dados = cursor.fetchall()

    if not dados:
        await ctx.send("⚠️ Nenhum tempo em pista registrado ainda.")
        cursor.close()
        conn.close()
        return

    msg = "**⏱️ Ranking Tempo em Pista:**\n"

    for i, (uid, pts) in enumerate(dados, 1):
        m = ctx.guild.get_member(uid)
        nome = m.display_name if m else f"ID:{uid}"

        linha = f"{i}. {nome} — {pts} tempo em pista\n"

        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""

        msg += linha

    if msg:
        await ctx.send(msg)

    cursor.close()
    conn.close()


# =========================
# 🆕 STATUS (COM TIERS + INATIVIDADE + TEMPO)
# =========================
@bot.command()
async def status(ctx, membro: discord.Member = None):
    membro = membro or ctx.author

    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT pontos, ultima_atividade FROM pontos WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos = r[0] if r else 0
    ultima_atividade = r[1] if r else None

    cursor.execute("SELECT user_id FROM pontos ORDER BY pontos DESC")
    ranking_dados = [uid for (uid,) in cursor.fetchall()]
    rank_pontos = ranking_dados.index(membro.id) + 1 if membro.id in ranking_dados else "N/A"

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_solo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_solo ORDER BY pontos DESC")
    ranking_dados_solo = [uid for (uid,) in cursor.fetchall()]
    rank_solo = ranking_dados_solo.index(membro.id) + 1 if membro.id in ranking_dados_solo else "N/A"

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_team = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_team ORDER BY pontos DESC")
    ranking_dados_team = [uid for (uid,) in cursor.fetchall()]
    rank_team = ranking_dados_team.index(membro.id) + 1 if membro.id in ranking_dados_team else "N/A"

    cursor.execute("SELECT pontos FROM pontos_tempo WHERE user_id = %s", (membro.id,))
    r = cursor.fetchone()
    pontos_tempo = r[0] if r else 0

    cursor.execute("SELECT user_id FROM pontos_tempo ORDER BY pontos DESC")
    ranking_dados_tempo = [uid for (uid,) in cursor.fetchall()]
    rank_tempo = ranking_dados_tempo.index(membro.id) + 1 if membro.id in ranking_dados_tempo else "N/A"

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

    tier_roles = {
        1: TIER_1_ROLE_ID,
        2: TIER_2_ROLE_ID,
        3: TIER_3_ROLE_ID,
        4: TIER_4_ROLE_ID,
        5: TIER_5_ROLE_ID,
    }

    cor = discord.Color.blurple()

    for role in membro.roles:
        for _, role_id in tier_roles.items():
            if role.id == role_id:
                if role.color.value != 0:
                    cor = role.color
                break

    embed = discord.Embed(
        title="🎮┃PERFIL DE JOGADOR",
        description=f"**{membro.display_name}**",
        color=cor
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
        name="⏱️ TEMPO EM PISTA",
        value=(
            f"🏅 Ranking: `#{rank_tempo}`\n"
            f"Total: **{pontos_tempo:02}**"
        ),
        inline=False
    )

    embed.add_field(
        name="🕒 ATIVIDADE",
        value=(
            f"Inativo há: **{formatar_inatividade(ultima_atividade)}**"
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


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != EMOJI_CONFIRMACAO:
        return

    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    if not await utilizador_e_admin_no_guild(guild, payload.user_id):
        return

    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT thread_id, user_id, nome_jogador, estado
        FROM inscricoes_jogos
        WHERE message_id = %s
    """, (payload.message_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return

    thread_id, user_id, nome_jogador, estado = row

    if estado != "pendente_pagamento":
        cursor.close()
        conn.close()
        return

    cursor.execute("""
        UPDATE inscricoes_jogos
        SET estado = 'pago',
            confirmado_por = %s,
            confirmado_em = NOW()
        WHERE message_id = %s
    """, (payload.user_id, payload.message_id))
    conn.commit()
    cursor.close()
    conn.close()

    canal = bot.get_channel(thread_id)
    user = bot.get_user(user_id)

    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None

    autor_inscricao_mention = f"<@{user_id}>"

    if canal:
        try:
            msg = await canal.fetch_message(payload.message_id)
            embed = await criar_embed_inscricao_paga(guild, canal.name, nome_jogador, autor_inscricao_mention)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

        await atualizar_embed_estado(canal)

    if user:
        try:
            await user.send(f"✅ A tua inscrição foi finalizada na thread **{canal.name if canal else 'do jogo'}**.")
        except (discord.Forbidden, discord.HTTPException):
            pass


@tasks.loop(minutes=1)
async def verificar_inscricoes():
    conn = get_connection()
    if not conn:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, thread_id, message_id, user_id, nome_jogador, expira_em, aviso_30min_enviado
        FROM inscricoes_jogos
        WHERE estado = 'pendente_pagamento'
    """)
    rows = cursor.fetchall()

    agora = utc_now()

    for inscricao_id, thread_id, message_id, user_id, nome_jogador, expira_em, aviso_30min_enviado in rows:
        if expira_em.tzinfo is None:
            expira_em = expira_em.replace(tzinfo=timezone.utc)

        tempo_restante = expira_em - agora

        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        if (not aviso_30min_enviado) and timedelta(minutes=0) < tempo_restante <= timedelta(minutes=MINUTOS_AVISO):
            if user:
                try:
                    await user.send(
                        "⏰ Faltam menos de 30 minutos para a tua inscrição expirar.\n"
                        "Se a inscrição não for finalizada a tempo, será cancelada."
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            cursor.execute("""
                UPDATE inscricoes_jogos
                SET aviso_30min_enviado = TRUE
                WHERE id = %s
            """, (inscricao_id,))
            conn.commit()

        if agora >= expira_em:
            cursor.execute("""
                UPDATE inscricoes_jogos
                SET estado = 'expirado'
                WHERE id = %s
            """, (inscricao_id,))
            conn.commit()

            canal = bot.get_channel(thread_id)
            guild = canal.guild if canal else None
            autor_inscricao_mention = f"<@{user_id}>"

            if canal:
                try:
                    msg = await canal.fetch_message(message_id)
                    embed = await criar_embed_inscricao_expirada(guild, canal.name, nome_jogador, autor_inscricao_mention)
                    await msg.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

                await atualizar_embed_estado(canal)

            if user:
                try:
                    await user.send(f"❌ A tua inscrição expirou porque passaram {HORAS_PAGAMENTO} horas sem ser finalizada.")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    cursor.close()
    conn.close()


# ---------- START ----------
bot.run(TOKEN)
