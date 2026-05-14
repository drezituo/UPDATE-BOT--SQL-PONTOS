import discord
from discord.ext import commands, tasks
import asyncio
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


def extrair_jogador_da_inscricao(ctx, nome: str):
    nome = normalizar_nome(nome)

    if ctx.message.mentions:
        membro_mencionado = ctx.message.mentions[0]
        return membro_mencionado.display_name, membro_mencionado

    return nome, None


def extrair_nome_cancelamento(ctx, nome: str):
    nome = normalizar_nome(nome)

    if ctx.message.mentions:
        membro_mencionado = ctx.message.mentions[0]
        return membro_mencionado.display_name, membro_mencionado

    return nome, None


async def encontrar_membro_por_nome(guild: discord.Guild, nome_jogador: str):
    if guild is None:
        return None

    nome_jogador = normalizar_nome(nome_jogador).lower()

    def corresponde(member: discord.Member) -> bool:
        candidatos = {
            normalizar_nome(member.display_name).lower(),
            normalizar_nome(member.name).lower(),
        }

        global_name = getattr(member, "global_name", None)
        if global_name:
            candidatos.add(normalizar_nome(global_name).lower())

        return nome_jogador in candidatos

    for member in guild.members:
        if corresponde(member):
            return member

    try:
        async for member in guild.fetch_members(limit=None):
            if corresponde(member):
                return member
    except (discord.Forbidden, discord.HTTPException):
        pass

    return None


async def obter_avatar_url_jogador(guild: discord.Guild, nome_jogador: str, membro: discord.Member = None):
    if membro is not None:
        return membro.display_avatar.url

    membro_encontrado = await encontrar_membro_por_nome(guild, nome_jogador)
    if membro_encontrado:
        return membro_encontrado.display_avatar.url

    return "https://cdn.discordapp.com/embed/avatars/0.png"


async def obter_valor_jogador_embed(guild: discord.Guild, nome_jogador: str, membro: discord.Member = None):
    if membro is not None:
        return membro.mention

    membro_encontrado = await encontrar_membro_por_nome(guild, nome_jogador)
    if membro_encontrado:
        return membro_encontrado.mention

    return nome_jogador


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


# ---------- EQUIPAS HELPERS ----------
def calcular_pontos_jogador_sync(user_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()

    cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (user_id,))
    r = cursor.fetchone()
    solo = r[0] if r else 0

    cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (user_id,))
    r = cursor.fetchone()
    team = r[0] if r else 0

    cursor.close()
    conn.close()

    total = solo + team

    return {
        "solo": solo,
        "team": team,
        "total": total,
    }


def calcular_pontos_equipa_sync(equipa_id: int):
    conn = get_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id
        FROM equipas_membros
        WHERE equipa_id = %s
    """, (equipa_id,))
    membros = cursor.fetchall()

    totais = {
        "solo": 0,
        "team": 0,
        "total": 0,
        "membros": len(membros),
    }

    for (user_id,) in membros:
        cursor.execute("SELECT pontos FROM pontos_solo WHERE user_id = %s", (user_id,))
        r = cursor.fetchone()
        solo = r[0] if r else 0

        cursor.execute("SELECT pontos FROM pontos_team WHERE user_id = %s", (user_id,))
        r = cursor.fetchone()
        team = r[0] if r else 0

        totais["solo"] += solo
        totais["team"] += team
        totais["total"] += solo + team

    cursor.close()
    conn.close()
    return totais

# ---------- EMBEDS INSCRIÇÕES ----------
async def criar_embed_inscricao_pendente(ctx, nome: str, expira_em: datetime, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(ctx.guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(ctx.guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição - {ctx.channel.name}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=ctx.author.mention, inline=True)
    embed.add_field(name="📌 Estado", value="⏳ Inscrição por finalizar", inline=False)
    embed.add_field(name="⏰ Expira", value=f"<t:{int(expira_em.timestamp())}:F>", inline=True)
    embed.add_field(name="⌛ Tempo restante", value=f"<t:{int(expira_em.timestamp())}:R>", inline=True)
    embed.set_footer(text=f"⚡ Tens {HORAS_PAGAMENTO} horas para concluir a inscrição")
    return embed


async def criar_embed_inscricao_paga(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição confirmada - {thread_name}",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="✅ Inscrição finalizada", inline=False)
    embed.set_footer(text="✅ Inscrição concluída com sucesso")
    return embed


async def criar_embed_inscricao_expirada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
    embed.add_field(name="📝 Autor da inscrição", value=autor_inscricao_mention, inline=True)
    embed.add_field(name="📌 Estado", value="❌ Inscrição expirada por falta de finalização", inline=False)
    embed.set_footer(text="⌛ O prazo para finalizar a inscrição terminou")
    return embed


async def criar_embed_inscricao_cancelada(guild: discord.Guild, thread_name: str, nome: str, autor_inscricao_mention: str, cancelado_por="", membro_jogador: discord.Member = None):
    avatar_url = await obter_avatar_url_jogador(guild, nome, membro_jogador)
    jogador_valor = await obter_valor_jogador_embed(guild, nome, membro_jogador)

    embed = discord.Embed(
        title=f"🎟️ Inscrição cancelada - {thread_name}",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="👤 Jogador", value=jogador_valor, inline=True)
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

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipas (
            id BIGSERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            logo_url TEXT,
            criado_por BIGINT NOT NULL,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS equipas_membros (
            equipa_id BIGINT NOT NULL REFERENCES equipas(id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            PRIMARY KEY (equipa_id, user_id)
        )
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
        if ctx.command and ctx.command.name in (
            "inscrever", "cancelarinscricao", "criarequipa", "adicionarmembroequipa",
            "removermembroequipa", "equipa", "apagarequipa", "mudarlogoequipa"
        ):
            await ctx.send("⚠️ Faltam argumentos no comando.", delete_after=10)
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
    nome_original = nome
    nome, membro_jogador = extrair_jogador_da_inscricao(ctx, nome_original)

    if not is_thread_channel(ctx.channel):
        await apagar_mensagem_comando(ctx)
        return await ctx.send("⚠️ Este comando só pode ser usado dentro da thread do jogo.", delete_after=10)

    if len(normalizar_nome(nome)) < 3:
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

    embed = await criar_embed_inscricao_pendente(ctx, nome, expira_em, membro_jogador)
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

    nome, membro_jogador = extrair_nome_cancelamento(ctx, nome)

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
            cancelado_por,
            membro_jogador
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
# 📜 LISTA DE COMANDOS
# =========================
@bot.command()
async def comandos(ctx):
    embed = discord.Embed(
        title="📜 Lista de Comandos",
        description="Aqui estão todos os comandos disponíveis no bot:",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(
        name="🎟️ Inscrições",
        value=(
            "`!abrir_inscricoes [limite]`\n"
            "`!fechar_inscricoes`\n"
            "`!estado_inscricoes`\n"
            "`!inscrever nome`\n"
            "`!cancelarinscricao nome`"
        ),
        inline=False
    )

    embed.add_field(
        name="⭐ Pontos / Presenças",
        value=(
            "`!addpontos @user quantidade`\n"
            "`!removepontos @user quantidade`\n"
            "`!pontos [@user]`\n"
            "`!ranking`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 Solo Rebirth",
        value=(
            "`!addsolo @user quantidade`\n"
            "`!removesolo @user quantidade`\n"
            "`!pontossolo [@user]`\n"
            "`!rankingsolo`"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 Team Win",
        value=(
            "`!addteam @user quantidade`\n"
            "`!removeteam @user quantidade`\n"
            "`!pontosteam [@user]`\n"
            "`!rankingteam`"
        ),
        inline=False
    )

    embed.add_field(
        name="⏱️ Tempo em Pista",
        value=(
            "`!addtempo @user quantidade`\n"
            "`!removetempo @user quantidade`\n"
            "`!tempopista [@user]`\n"
            "`!rankingtempo`"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Equipas",
        value=(
            "`!criarequipa nome [logo_url]`\n"
            "`!adicionarmembroequipa nome @user`\n"
            "`!removermembroequipa nome @user`\n"
            "`!mudarlogoequipa nome url`\n"
            "`!apagarequipa nome`\n"
            "`!equipa nome`\n"
            "`!rankingequipas`\n"
            "`!listarequipas`"
        ),
        inline=False
    )

    embed.add_field(
        name="🎮 Perfil / Utilidades",
        value=(
            "`!status [@user]`\n"
            "`!ping`\n"
            "`!comandos`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔐 Nota",
        value="Comandos de adicionar/remover pontos, inscrições admin e gestão de equipas precisam de permissão de administrador.",
        inline=False
    )

    embed.set_footer(text="⚡ Prefixo do bot: !")

    await ctx.send(embed=embed)


# =========================
# 🛡️ SISTEMA DE EQUIPAS
# =========================
@bot.command()
@commands.has_permissions(administrator=True)
async def criarequipa(ctx, nome: str, logo_url: str = None):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO equipas (nome, logo_url, criado_por)
            VALUES (%s, %s, %s)
        """, (nome, logo_url, ctx.author.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        print(f"Erro ao criar equipa: {e}")
        return await ctx.send("⚠️ Já existe uma equipa com esse nome ou ocorreu um erro.")

    cursor.close()
    conn.close()

    embed = discord.Embed(
        title="🛡️ Nova Equipa Criada",
        description=f"A equipa **{nome}** foi criada com sucesso!",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )
    if logo_url:
        embed.set_thumbnail(url=logo_url)
    embed.add_field(name="👑 Criada por", value=ctx.author.mention, inline=True)
    embed.add_field(name="👥 Membros", value="Nenhum membro ainda", inline=True)
    embed.set_footer(text="Sistema competitivo de equipas ativo")

    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def adicionarmembroequipa(ctx, nome_equipa: str, membro: discord.Member):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa = cursor.fetchone()

    if not equipa:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome = equipa

    try:
        cursor.execute("""
            INSERT INTO equipas_membros (equipa_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (equipa_id, membro.id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        print(f"Erro ao adicionar membro à equipa: {e}")
        return await ctx.send("⚠️ Erro ao adicionar membro à equipa.")

    cursor.close()
    conn.close()

    await ctx.send(f"✅ {membro.mention} foi adicionado à equipa **{nome}**.")


@bot.command()
@commands.has_permissions(administrator=True)
async def removermembroequipa(ctx, nome_equipa: str, membro: discord.Member):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa = cursor.fetchone()

    if not equipa:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome = equipa

    cursor.execute("""
        DELETE FROM equipas_membros
        WHERE equipa_id = %s AND user_id = %s
    """, (equipa_id, membro.id))
    conn.commit()
    cursor.close()
    conn.close()

    await ctx.send(f"❌ {membro.mention} foi removido da equipa **{nome}**.")


@bot.command()
@commands.has_permissions(administrator=True)
async def mudarlogoequipa(ctx, nome_equipa: str, logo_url: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        UPDATE equipas
        SET logo_url = %s
        WHERE LOWER(nome) = LOWER(%s)
    """, (logo_url, nome_equipa))
    alteradas = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if alteradas == 0:
        return await ctx.send("⚠️ Essa equipa não existe.")

    await ctx.send(f"✅ Logo da equipa **{nome_equipa}** atualizado.")


@bot.command()
@commands.has_permissions(administrator=True)
async def apagarequipa(ctx, *, nome_equipa: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    alteradas = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()

    if alteradas == 0:
        return await ctx.send("⚠️ Essa equipa não existe.")

    await ctx.send(f"🗑️ A equipa **{nome_equipa}** foi apagada.")


@bot.command()
async def equipa(ctx, *, nome_equipa: str):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, logo_url
        FROM equipas
        WHERE LOWER(nome) = LOWER(%s)
    """, (nome_equipa,))
    equipa_row = cursor.fetchone()

    if not equipa_row:
        cursor.close()
        conn.close()
        return await ctx.send("⚠️ Essa equipa não existe.")

    equipa_id, nome, logo_url = equipa_row

    cursor.execute("""
        SELECT user_id
        FROM equipas_membros
        WHERE equipa_id = %s
    """, (equipa_id,))
    membros = cursor.fetchall()

    cursor.close()
    conn.close()

    pontos = calcular_pontos_equipa_sync(equipa_id)
    if pontos is None:
        return await ctx.send(DB_ERROR_MSG)

    lista_membros = ""
    for (uid,) in membros:
        membro = ctx.guild.get_member(uid)
        nome_membro = membro.mention if membro else f"<@{uid}>"
        dados = calcular_pontos_jogador_sync(uid)
        total_membro = dados["total"] if dados else 0
        lista_membros += f"• {nome_membro} — **{total_membro} pts**\n"

    if not lista_membros:
        lista_membros = "Nenhum membro nesta equipa."

    embed = discord.Embed(
        title=f"🛡️ Equipa {nome}",
        description="Perfil competitivo da equipa",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )

    if logo_url:
        embed.set_thumbnail(url=logo_url)

    embed.add_field(name="👥 Membros", value=lista_membros[:1024], inline=False)
    embed.add_field(
        name="📊 Pontos da Equipa",
        value=(
            f"🔥 Solo Rebirth: **{pontos['solo']}**\n"
            f"👥 Team Wins: **{pontos['team']}**\n\n"
            f"🏆 Total: **{pontos['total']} pontos**"
        ),
        inline=False
    )
    embed.set_footer(text=f"{pontos['membros']} membro(s) na equipa")

    await ctx.send(embed=embed)


@bot.command()
async def rankingequipas(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, logo_url
        FROM equipas
    """)
    equipas = cursor.fetchall()
    cursor.close()
    conn.close()

    if not equipas:
        return await ctx.send("⚠️ Ainda não existem equipas criadas.")

    ranking = []

    for equipa_id, nome, logo_url in equipas:
        pontos = calcular_pontos_equipa_sync(equipa_id)
        if pontos:
            ranking.append({
                "nome": nome,
                "logo_url": logo_url,
                "total": pontos["total"],
                "solo": pontos["solo"],
                "team": pontos["team"],
            })

    ranking.sort(key=lambda x: x["total"], reverse=True)

    if not ranking:
        return await ctx.send("⚠️ Ainda não existem equipas com pontos.")

    medalhas = [
        ("🥇 1.º Lugar", discord.Color.gold()),
        ("🥈 2.º Lugar", discord.Color.light_grey()),
        ("🥉 3.º Lugar", discord.Color.orange()),
    ]

    for i, equipa_info in enumerate(ranking[:3]):
        titulo, cor = medalhas[i]

        embed = discord.Embed(
            title=titulo,
            description=f"🛡️ **{equipa_info['nome']}**",
            color=cor,
            timestamp=discord.utils.utcnow()
        )

        if equipa_info["logo_url"]:
            embed.set_thumbnail(url=equipa_info["logo_url"])

        embed.add_field(
            name="🏆 Pontos",
            value=(
                f"**Total:** {equipa_info['total']} pontos\n"
                f"🔥 Solo Rebirth: {equipa_info['solo']}\n"
                f"👥 Team Wins: {equipa_info['team']}"
            ),
            inline=False
        )

        embed.set_footer(text="💡 Para ver mais equipas usa !listarequipas")

        await ctx.send(embed=embed)

@bot.command()
async def listarequipas(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)

    cursor = conn.cursor()
    cursor.execute("SELECT id, nome FROM equipas ORDER BY nome ASC")
    equipas = cursor.fetchall()
    cursor.close()
    conn.close()

    if not equipas:
        return await ctx.send("⚠️ Ainda não existem equipas criadas.")

    msg = "**🛡️ Equipas criadas:**\n"
    for _, nome in equipas:
        linha = f"• {nome}\n"
        if len(msg) + len(linha) > 2000:
            await ctx.send(msg)
            msg = ""
        msg += linha

    if msg:
        await ctx.send(msg)


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
    cursor.execute("UPDATE pontos SET pontos = %s, ultima_atividade = NOW() WHERE user_id = %s", (novo_total, membro.id))

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

    await ctx.send(f"👥 {membro.display_name} agora tem **{total} vitórias no TeamWin**")


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

    await ctx.send(f"❌ {membro.display_name} agora tem **{total} vitórias no TeamWin**")


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

    await ctx.send(f"👥 {membro.display_name} tem **{total} vitórias no TeamWin!**")


@bot.command()
async def rankingteam(ctx):
    conn = get_connection()
    if not conn:
        return await ctx.send(DB_ERROR_MSG)
    cursor = conn.cursor()

    cursor.execute("SELECT user_id, pontos FROM pontos_team ORDER BY pontos DESC")
    dados = cursor.fetchall()

    msg = "**🏆 Ranking TeamWin:**\n"

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
        name="👥 TEAM WIN",
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




# =========================
# 🎖️ MILSIM — OPERAÇÃO DUALITY
# =========================

COMANDO_CHANNEL_ID = 1504600088289214474
AZUL_CHANNEL_ID = 1504600244015599746
VERMELHO_CHANNEL_ID = 1504600284935094404
LOGS_CHANNEL_ID = 1504600337544122448
GM_CHANNEL_ID = 1504600378988171495

AZUL_ROLE_ID = 1504599233137868961
VERMELHO_ROLE_ID = 1504599117488455832
GM_ROLE_ID = 1504602388496121928

MILSPEED = {
    "decryption_seconds": 600,
    "blackout_seconds": 600
}

milsim_state = {
    "active": False,
    "scores": {"azul": 0, "vermelho": 0},
    "teams": {
        "azul": {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        },
        "vermelho": {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        }
    },
    "captured_players": []
}


MISSION_CODES = {
    "SHADOW-214": {
        "team": "azul",
        "mission": "mission_1",
        "points": 10,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "        ATUALIZAÇÃO TÁTICA\n"
            "╚══════════════════════════════╝\n\n"
            "〔 TASK FORCE AZUL 〕\n\n"
            "AUTORIZAÇÃO VALIDADA\n"
            "CÓDIGO: SHADOW-214\n\n"
            "Hard drive recuperada com sucesso.\n\n"
            "Regressem imediatamente ao COMANDO para reorganização operacional.\n\n"
            "ATENÇÃO:\n"
            "Elevada probabilidade de interceção inimiga durante retirada.\n\n"
            "Utilizem:\n"
            "`!reagrupado`\n\n"
            "assim que toda a unidade estiver pronta."
        ),
        "enemy_alert": (
            "⚠️ **ALERTA DE COMBATE**\n\n"
            "Movimentação inimiga confirmada no setor CQB.\n"
            "Possível extração de dados em progresso."
        )
    },

    "VIPER-771": {
        "team": "vermelho",
        "mission": "mission_1",
        "points": 10,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "        ATUALIZAÇÃO TÁTICA\n"
            "╚══════════════════════════════╝\n\n"
            "〔 TASK FORCE VERMELHA 〕\n\n"
            "AUTORIZAÇÃO VALIDADA\n"
            "CÓDIGO: VIPER-771\n\n"
            "Protocolo de destruição parcialmente ativado.\n\n"
            "Regressem imediatamente ao COMANDO CENTRAL.\n\n"
            "Aguardem nova janela operacional.\n\n"
            "Utilizem:\n"
            "`!reagrupado`\n\n"
            "assim que toda a unidade estiver pronta."
        ),
        "enemy_alert": (
            "⚠️ **ALERTA DE COMBATE**\n\n"
            "Protocolo inimigo detetado no CQB.\n"
            "Dados comprometidos parcialmente."
        )
    },

    "BUNKER-551": {
        "team": "azul",
        "mission": "mission_2",
        "points": 15,
        "type": "decryption",
        "message": (
            "╔══════════════════════════════╗\n"
            "          TERMINAL ONLINE\n"
            "╚══════════════════════════════╝\n\n"
            "SEQUÊNCIA DE DESENCRIPTAÇÃO INICIADA\n\n"
            "TEMPO ESTIMADO:\n"
            "**10 MINUTOS**\n\n"
            "Defendam o perímetro do BUNKER a todo o custo."
        ),
        "enemy_alert": (
            "🚨 **ALERTA — BUNKER**\n\n"
            "Atividade inimiga detetada no BUNKER.\n"
            "Desencriptação iniciada.\n\n"
            "Tempo estimado: **10 minutos**.\n\n"
            "Objetivo prioritário: interromper terminal."
        )
    },

    "RAVEN-119": {
        "team": "vermelho",
        "mission": "mission_2",
        "points": 15,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "        SABOTAGEM CONFIRMADA\n"
            "╚══════════════════════════════╝\n\n"
            "FALHA NO UPLINK\n\n"
            "Transmissão inimiga interrompida.\n"
            "Terminal temporariamente comprometido.\n\n"
            "Regressem ao COMANDO para reorganização."
        ),
        "enemy_alert": (
            "🚨 **FALHA NO TERMINAL**\n\n"
            "A desencriptação foi interrompida.\n"
            "Recuperem controlo do BUNKER imediatamente."
        )
    },

    "GHOST-802": {
        "team": "azul",
        "mission": "mission_3",
        "points": 20,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "          HVT CAPTURADO\n"
            "╚══════════════════════════════╝\n\n"
            "Operador inimigo confirmado sob custódia.\n\n"
            "OBJETIVO:\n"
            "▸ escoltar HVT até SAFEZONE\n"
            "▸ proteger alvo vivo\n"
            "▸ regressar ao COMANDO após extração\n\n"
            "**+20 pontos atribuídos**"
        ),
        "enemy_alert": (
            "🚨 **ALERTA HVT**\n\n"
            "O vosso operador prioritário foi capturado.\n"
            "Recuperem o alvo antes da extração."
        )
    },

    "EXFIL-337": {
        "team": "vermelho",
        "mission": "mission_3",
        "points": 20,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "        EXTRAÇÃO CONFIRMADA\n"
            "╚══════════════════════════════╝\n\n"
            "VIP protegido com sucesso.\n"
            "Evacuação concluída.\n\n"
            "Regressem ao COMANDO para nova janela operacional.\n\n"
            "**+20 pontos atribuídos**"
        ),
        "enemy_alert": (
            "⚠️ **ALVO PERDIDO**\n\n"
            "O VIP inimigo concluiu extração.\n"
            "Preparem nova fase operacional."
        )
    },

    "OMEGA-440": {
        "team": "azul",
        "mission": "mission_4",
        "points": 20,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "        TRANSMISSÃO ATIVADA\n"
            "╚══════════════════════════════╝\n\n"
            "Uplink operacional no BUNKER.\n"
            "Preparem defesa para fase final.\n\n"
            "**+20 pontos atribuídos**"
        ),
        "enemy_alert": (
            "📡 **TRANSMISSÃO INIMIGA ATIVA**\n\n"
            "O inimigo ativou uplink no BUNKER.\n"
            "Preparem corte de transmissão."
        )
    },

    "BLACK-916": {
        "team": "vermelho",
        "mission": "mission_4",
        "points": 20,
        "type": "complete",
        "message": (
            "╔══════════════════════════════╗\n"
            "          RELAY DESTRUÍDO\n"
            "╚══════════════════════════════╝\n\n"
            "Transmissão inimiga comprometida.\n"
            "Preparem assalto final ao BUNKER.\n\n"
            "**+20 pontos atribuídos**"
        ),
        "enemy_alert": (
            "💥 **RELAY COMPROMETIDO**\n\n"
            "A vossa transmissão foi cortada.\n"
            "Recuperem controlo antes da fase final."
        )
    },

    "NOVA-999": {
        "team": "azul",
        "mission": "final",
        "points": 30,
        "type": "end",
        "message": (
            "🏆 **TRANSMISSÃO FINAL CONCLUÍDA**\n\n"
            "Vitória operacional da TASK FORCE AZUL."
        )
    },

    "IRON-666": {
        "team": "vermelho",
        "mission": "final",
        "points": 30,
        "type": "end",
        "message": (
            "🏆 **TRANSMISSÃO INIMIGA IMPEDIDA**\n\n"
            "Vitória operacional da TASK FORCE VERMELHA."
        )
    }
}


NEXT_MISSIONS = {
    "mission_1": {
        "azul": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE AZUL 〕\n"
            "MISSÃO 02 — SIGNAL KEY\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Os dados recuperados devem ser desencriptados na estação BUNKER.\n\n"
            "OBJETIVOS PRINCIPAIS:\n"
            "▸ transportar dispositivo\n"
            "▸ ativar terminal\n"
            "▸ defender transmissão\n\n"
            "CÓDIGO DO TERMINAL:\n"
            "**BUNKER-551**"
        ),
        "vermelho": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE VERMELHA 〕\n"
            "MISSÃO 02 — SIGNAL KEY\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Movimentação inimiga detetada em direção ao BUNKER.\n\n"
            "OBJETIVOS PRINCIPAIS:\n"
            "▸ intercetar transporte\n"
            "▸ sabotar uplink\n"
            "▸ impedir desencriptação\n\n"
            "CÓDIGO DE SABOTAGEM:\n"
            "**RAVEN-119**"
        )
    },
    "mission_2": {
        "azul": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE AZUL 〕\n"
            "MISSÃO 03 — HVT CAPTURE\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Um operador inimigo de elevado valor tenta escapar pelos setores oeste.\n\n"
            "OBJETIVOS PRINCIPAIS:\n"
            "▸ capturar HVT vivo\n"
            "▸ escoltar alvo até SAFEZONE\n"
            "▸ garantir extração da intel\n\n"
            "CÓDIGO HVT:\n"
            "**GHOST-802**"
        ),
        "vermelho": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE VERMELHA 〕\n"
            "MISSÃO 03 — VIPER ESCORT\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Protejam o operador prioritário e garantam evacuação segura.\n\n"
            "OBJETIVOS PRINCIPAIS:\n"
            "▸ impedir captura\n"
            "▸ manter movimentação\n"
            "▸ concluir extração\n\n"
            "CÓDIGO SAFEZONE:\n"
            "**EXFIL-337**"
        )
    },
    "mission_3": {
        "azul": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE AZUL 〕\n"
            "MISSÃO 04 — BLACKOUT\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Ativem transmissão no BUNKER durante falha de comunicações.\n\n"
            "OBJETIVOS:\n"
            "▸ controlar BUNKER\n"
            "▸ ativar uplink\n"
            "▸ preparar defesa final\n\n"
            "CÓDIGO:\n"
            "**OMEGA-440**"
        ),
        "vermelho": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE VERMELHA 〕\n"
            "MISSÃO 04 — BLACKOUT\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Cortem a transmissão inimiga e destruam o relay.\n\n"
            "OBJETIVOS:\n"
            "▸ infiltrar BUNKER\n"
            "▸ destruir relay\n"
            "▸ impedir uplink inimigo\n\n"
            "CÓDIGO:\n"
            "**BLACK-916**"
        )
    },
    "mission_4": {
        "azul": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE AZUL 〕\n"
            "MISSÃO FINAL — LAST TRANSMISSION\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Controlem o BUNKER e concluam transmissão final.\n\n"
            "OBJETIVOS FINAIS:\n"
            "▸ controlar bunker\n"
            "▸ defender terminal\n"
            "▸ concluir upload final\n\n"
            "CÓDIGO FINAL:\n"
            "**NOVA-999**"
        ),
        "vermelho": (
            "━━━━━━━━━━━━━━━━━━\n"
            "〔 TASK FORCE VERMELHA 〕\n"
            "MISSÃO FINAL — LAST TRANSMISSION\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Impeçam transmissão inimiga e controlem o BUNKER.\n\n"
            "OBJETIVOS FINAIS:\n"
            "▸ destruir relay\n"
            "▸ eliminar operadores de transmissão\n"
            "▸ controlar bunker\n\n"
            "CÓDIGO FINAL:\n"
            "**IRON-666**"
        )
    }
}


def milsim_get_channel(channel_id: int):
    return bot.get_channel(channel_id)


def milsim_team_from_channel(channel_id: int):
    if channel_id == AZUL_CHANNEL_ID:
        return "azul"
    if channel_id == VERMELHO_CHANNEL_ID:
        return "vermelho"
    return None


def milsim_enemy(team: str):
    return "vermelho" if team == "azul" else "azul"


def milsim_channel_for_team(team: str):
    return milsim_get_channel(AZUL_CHANNEL_ID if team == "azul" else VERMELHO_CHANNEL_ID)


async def milsim_log(texto: str):
    logs = milsim_get_channel(LOGS_CHANNEL_ID)
    if logs:
        await logs.send(texto)


async def milsim_send_to_team(team: str, texto: str):
    canal = milsim_channel_for_team(team)
    if canal:
        await canal.send(texto)


def milsim_is_gm(ctx):
    return any(role.id == GM_ROLE_ID for role in ctx.author.roles) or ctx.author.guild_permissions.administrator


async def milsim_start_decryption(team: str):
    await asyncio.sleep(MILSPEED["decryption_seconds"])

    if not milsim_state["active"]:
        return

    team_state = milsim_state["teams"][team]

    if team_state["current"] != "mission_2":
        return

    milsim_state["scores"][team] += 10
    team_state["phase"] = "regroup"
    team_state["regrouped"] = False

    await milsim_send_to_team(
        team,
        "✅ **DESENCRIPTAÇÃO CONCLUÍDA**\n\n"
        "+10 pontos atribuídos.\n\n"
        "Regressem ao COMANDO e usem `!reagrupado`."
    )

    await milsim_log(f"✅ Desencriptação concluída para **{team.upper()}**. +10 pontos.")


@bot.command()
async def start_op(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem iniciar a operação.", delete_after=10)

    milsim_state["active"] = True
    milsim_state["scores"] = {"azul": 0, "vermelho": 0}
    milsim_state["captured_players"] = []

    for team in ["azul", "vermelho"]:
        milsim_state["teams"][team] = {
            "current": "mission_1",
            "phase": "mission",
            "regrouped": False,
            "completed_codes": []
        }

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)

    if comando:
        await comando.send(
            "╔══════════════════════════════╗\n"
            "        COMANDO CENTRAL\n"
            "       OPERAÇÃO: DUALITY\n"
            "╚══════════════════════════════╝\n\n"
            "〔 TRANSMISSÃO GLOBAL 〕\n\n"
            "Foi intercetada atividade militar dentro do complexo industrial abandonado.\n\n"
            "Duas forças hostis disputam controlo sobre:\n"
            "▸ inteligência classificada\n"
            "▸ sistemas de transmissão\n"
            "▸ operadores inimigos\n"
            "▸ corredores de extração\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "REGRAS OPERACIONAIS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "▸ Códigos físicos desbloqueiam operações\n"
            "▸ Operadores capturados podem conter intel\n"
            "▸ HVTs devem ser capturados vivos\n"
            "▸ Todas as unidades DEVEM regressar ao HQ após cada missão\n"
            "▸ Aguardem novas ordens após reorganização\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "INÍCIO DA OPERAÇÃO:\n"
            "T-60 SEGUNDOS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Boa caça, operadores."
        )

    await milsim_send_to_team(
        "azul",
        "━━━━━━━━━━━━━━━━━━\n"
        "〔 TASK FORCE AZUL 〕\n"
        "MISSÃO 01 — DEAD DROP\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Reconhecimento aéreo confirma a existência de uma hard drive escondida no edifício CQB.\n\n"
        "OBJETIVOS PRINCIPAIS:\n"
        "▸ infiltrar estrutura\n"
        "▸ recuperar dispositivo\n"
        "▸ extrair dados em segurança\n\n"
        "CÓDIGO DE AUTORIZAÇÃO:\n"
        "**SHADOW-214**\n\n"
        "CONDIÇÃO ESPECIAL:\n"
        "O operador que transportar o dispositivo:\n"
        "▸ não pode correr\n"
        "▸ apenas pistola autorizada"
    )

    await milsim_send_to_team(
        "vermelho",
        "━━━━━━━━━━━━━━━━━━\n"
        "〔 TASK FORCE VERMELHA 〕\n"
        "MISSÃO 01 — DEAD DROP\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Forças inimigas tentam recuperar informação crítica dentro do CQB.\n\n"
        "OBJETIVOS PRINCIPAIS:\n"
        "▸ localizar protocolo de destruição\n"
        "▸ impedir extração inimiga\n"
        "▸ garantir controlo interno do edifício\n\n"
        "CÓDIGO DE AUTORIZAÇÃO:\n"
        "**VIPER-771**"
    )

    await milsim_log("🎖️ Operação DUALITY iniciada.")
    await ctx.send("✅ Operação iniciada.")


@bot.command()
async def codigo(ctx, codigo: str):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.", delete_after=10)

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação ainda não está ativa.", delete_after=10)

    codigo = codigo.upper().strip()

    if codigo not in MISSION_CODES:
        return await ctx.send("❌ Código inválido ou intel comprometida.", delete_after=10)

    data = MISSION_CODES[codigo]

    if data["team"] != team:
        return await ctx.send("❌ Este código não pertence à tua cadeia operacional.", delete_after=10)

    team_state = milsim_state["teams"][team]

    if codigo in team_state["completed_codes"]:
        return await ctx.send("⚠️ Este código já foi utilizado.", delete_after=10)

    if team_state["current"] != data["mission"]:
        return await ctx.send("⚠️ Código correto, mas fora da fase operacional atual.", delete_after=10)

    team_state["completed_codes"].append(codigo)
    milsim_state["scores"][team] += data["points"]

    await ctx.send(data["message"])

    enemy_alert = data.get("enemy_alert")
    if enemy_alert:
        await milsim_send_to_team(milsim_enemy(team), enemy_alert)

    await milsim_log(f"🔐 Código `{codigo}` validado por **{team.upper()}**. +{data['points']} pontos.")

    if data["type"] == "decryption":
        asyncio.create_task(milsim_start_decryption(team))
        return

    if data["type"] == "end":
        milsim_state["active"] = False
        await milsim_log("🏁 Operação terminada por código final.")
        return

    team_state["phase"] = "regroup"
    team_state["regrouped"] = False


@bot.command()
async def reagrupado(ctx):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.", delete_after=10)

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação não está ativa.", delete_after=10)

    team_state = milsim_state["teams"][team]

    if team_state["phase"] != "regroup":
        return await ctx.send("⚠️ A tua equipa ainda não está em fase de reorganização.", delete_after=10)

    team_state["regrouped"] = True

    await ctx.send(
        "✅ **REAGRUPAMENTO CONFIRMADO**\n\n"
        "Aguardem nova janela operacional."
    )

    await milsim_log(f"✅ **{team.upper()}** confirmou reagrupamento.")

    other = milsim_enemy(team)

    if milsim_state["teams"][other]["regrouped"]:
        old_mission = team_state["current"]

        if old_mission not in NEXT_MISSIONS:
            return

        for t in ["azul", "vermelho"]:
            milsim_state["teams"][t]["phase"] = "mission"
            milsim_state["teams"][t]["regrouped"] = False

            if old_mission == "mission_4":
                milsim_state["teams"][t]["current"] = "final"
            else:
                next_number = int(old_mission.split("_")[1]) + 1
                milsim_state["teams"][t]["current"] = f"mission_{next_number}"

            await milsim_send_to_team(t, NEXT_MISSIONS[old_mission][t])

        await milsim_log("📡 Nova fase operacional transmitida às duas equipas.")


@bot.command()
async def capturar(ctx, player_id: str, setor: str):
    team = milsim_team_from_channel(ctx.channel.id)

    if not team:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal da tua equipa.", delete_after=10)

    if not milsim_state["active"]:
        return await ctx.send("⚠️ A operação não está ativa.", delete_after=10)

    player_id = player_id.upper().strip()
    setor = setor.upper().strip()

    valid_prefix = "IRON-" if team == "azul" else "NOVA-"

    if not player_id.startswith(valid_prefix):
        return await ctx.send("❌ Esse operador não pertence à equipa inimiga.", delete_after=10)

    if player_id in milsim_state["captured_players"]:
        return await ctx.send("⚠️ Esse operador já foi capturado anteriormente.", delete_after=10)

    milsim_state["captured_players"].append(player_id)
    milsim_state["scores"][team] += 5

    await ctx.send(
        "╔══════════════════════════════╗\n"
        "        OPERADOR CAPTURADO\n"
        "╚══════════════════════════════╝\n\n"
        f"ID confirmado: **{player_id}**\n"
        f"Setor: **{setor}**\n\n"
        "INTEL RECUPERADA:\n"
        "▸ atividade rádio parcial\n"
        "▸ possível movimentação inimiga no setor indicado\n\n"
        "**+5 pontos atribuídos**"
    )

    await milsim_log(f"🪪 **{team.upper()}** capturou `{player_id}` no setor `{setor}`. +5 pontos.")


@bot.command()
async def opstatus(ctx):
    if ctx.channel.id not in [AZUL_CHANNEL_ID, VERMELHO_CHANNEL_ID, GM_CHANNEL_ID]:
        return await ctx.send("⚠️ Comando disponível apenas em canais operacionais.", delete_after=10)

    embed = discord.Embed(
        title="📡 Estado da Operação DUALITY",
        color=discord.Color.dark_gold(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(name="Estado", value="Ativa" if milsim_state["active"] else "Inativa", inline=True)
    embed.add_field(name="Score Azul", value=str(milsim_state["scores"]["azul"]), inline=True)
    embed.add_field(name="Score Vermelho", value=str(milsim_state["scores"]["vermelho"]), inline=True)

    for team in ["azul", "vermelho"]:
        st = milsim_state["teams"][team]
        embed.add_field(
            name=f"Equipa {team.upper()}",
            value=(
                f"Missão: `{st['current']}`\n"
                f"Fase: `{st['phase']}`\n"
                f"Reagrupado: `{st['regrouped']}`"
            ),
            inline=False
        )

    await ctx.send(embed=embed)


@bot.command()
async def score(ctx):
    await ctx.send(
        "🏆 **SCORE OPERACIONAL**\n\n"
        f"🔵 Azul: **{milsim_state['scores']['azul']} pts**\n"
        f"🔴 Vermelho: **{milsim_state['scores']['vermelho']} pts**"
    )


@bot.command()
async def gm_blackout(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    msg = (
        "━━━━━━━━━━━━━━━━━━\n"
        "〔 ALERTA GLOBAL 〕\n"
        "BLACKOUT\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "FALHA GENERALIZADA DE COMUNICAÇÕES\n\n"
        "CONDIÇÕES NO TERRENO:\n"
        "▸ lanternas proibidas\n"
        "▸ comunicações limitadas\n"
        "▸ apenas squad leaders autorizados em rádio\n"
        "▸ duração estimada: 10 minutos\n\n"
        "Mantenham eficácia operacional."
    )

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)
    if comando:
        await comando.send(msg)

    await milsim_send_to_team("azul", msg)
    await milsim_send_to_team("vermelho", msg)
    await milsim_log("⚫ Blackout ativado pelo Game Master.")
    await ctx.send("✅ Blackout enviado.")


@bot.command()
async def gm_next(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    old_mission = milsim_state["teams"]["azul"]["current"]

    if old_mission not in NEXT_MISSIONS:
        return await ctx.send("⚠️ Não existe próxima fase configurada.")

    for t in ["azul", "vermelho"]:
        milsim_state["teams"][t]["phase"] = "mission"
        milsim_state["teams"][t]["regrouped"] = False

        if old_mission == "mission_4":
            milsim_state["teams"][t]["current"] = "final"
        else:
            next_number = int(old_mission.split("_")[1]) + 1
            milsim_state["teams"][t]["current"] = f"mission_{next_number}"

        await milsim_send_to_team(t, NEXT_MISSIONS[old_mission][t])

    await milsim_log("⏭️ Game Master forçou próxima fase operacional.")
    await ctx.send("✅ Próxima fase enviada.")


@bot.command()
async def gm_end(ctx):
    if ctx.channel.id != GM_CHANNEL_ID:
        return await ctx.send("⚠️ Este comando só pode ser usado no canal GM.", delete_after=10)

    if not milsim_is_gm(ctx):
        return await ctx.send("❌ Apenas Game Masters podem usar este comando.", delete_after=10)

    milsim_state["active"] = False

    azul = milsim_state["scores"]["azul"]
    vermelho = milsim_state["scores"]["vermelho"]

    if azul > vermelho:
        vencedor = "🔵 TASK FORCE AZUL"
    elif vermelho > azul:
        vencedor = "🔴 TASK FORCE VERMELHA"
    else:
        vencedor = "EMPATE OPERACIONAL"

    msg = (
        "╔══════════════════════════════╗\n"
        "        FIM DA OPERAÇÃO\n"
        "╚══════════════════════════════╝\n\n"
        f"🔵 Azul: **{azul} pts**\n"
        f"🔴 Vermelho: **{vermelho} pts**\n\n"
        f"VENCEDOR:\n"
        f"**{vencedor}**\n\n"
        "COMANDO CENTRAL TERMINA LIGAÇÃO."
    )

    comando = milsim_get_channel(COMANDO_CHANNEL_ID)
    if comando:
        await comando.send(msg)

    await milsim_log("🏁 Operação terminada manualmente.")
    await ctx.send("✅ Operação terminada.")

# ---------- START ----------
bot.run(TOKEN)
