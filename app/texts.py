"""All user-facing strings (pt-BR: the audience is the Brazilian stream). Change here to translate."""

FEED_UNAVAILABLE = "Vídeo offline ou vídeo sem transcrições"
TITLE = "Bingo da Live"
TAGLINE = "Marque as expressões que aparecerem na live!"
START_HEADING = "Nenhuma partida em andamento"
START_HELP = "Você será o primeiro: comece a partida."
START_BUTTON = "Começar partida"
JOIN_HEADING = "Tem uma partida rolando!"
SITE_TITLE = "Jogos da Live"
NOT_FOUND = "Página não encontrada."
BACK_HOME = "Voltar ao início"
ADMIN_LOCKED = "Muitas tentativas erradas. Tente de novo em alguns minutos."


def players_in_game(n: int) -> str:
    return f"{n} jogador na partida." if n == 1 else f"{n} jogadores na partida."
JOIN_BUTTON = "Entrar na partida"
NAME_LABEL = "Seu nome (máx. 20 letras)"
NAME_HEADING = "Qual é o seu nome?"
NAME_HELP = "Escolha um nome para jogar. Ele vale para todos os jogos."
NAME_BUTTON = "Continuar"
PLAYING_AS = "Jogando como"
CHANGE_NAME = "trocar nome"
YOU = "você"
MARKED = "Marcadas"
LEADERBOARD = "Placar"
HOW_TO = "Toque numa casa quando ouvir (ou ler no chat) a expressão. Toque de novo para desmarcar."
GAME_OVER = "Fim de jogo!"
WINNER = "Vencedor(a): {name} com {n} casas marcadas"
NO_WINNER = "Ninguém marcou casas desta vez."
YOUR_RESULT = "Você marcou {n} casas e ficou em {rank}º lugar."
AVATAR_ALT = "Logo do canal"

ERRORS = {
    "invalid_name": "Nome inválido: use de 1 a 20 caracteres, sem símbolos de controle.",
    "name_taken": "Esse nome já está em uso nesta partida. Escolha outro.",
    "ip_limit_reached": "Já há {n} jogadores entrando desta rede, que é o limite. Peça ao apresentador para liberar mais vagas.",
    "session_already_active": "Alguém acabou de começar a partida. Entre nela!",
    "no_active_session": "A partida acabou ou ainda não começou.",
    "no_phrases": "Não foi possível montar sua cartela (poucas expressões cadastradas).",
    "feed_unavailable": FEED_UNAVAILABLE,
    "feed_down": FEED_UNAVAILABLE,
    "no_source": "Não há partida disponível agora: o canal não está ao vivo na horizontal. Aguarde o administrador iniciar uma partida.",
    "invalid_video": "Link inválido. Use um link do YouTube (youtube.com ou youtu.be).",
    "generic": "Algo deu errado. Tente de novo.",
}

ADMIN_ACTIVE = "Partida em andamento"
ADMIN_ENDED = "Partida encerrada"
ADMIN_STARTED = "Início"
ADMIN_ENDED_AT = "Fim"
ADMIN_PLAYERS = "Jogadores"
ADMIN_WINNER = "Vencedor(a)"
ADMIN_FINISH = "Encerrar partida"
ADMIN_FINISH_CONFIRM = "Encerrar a partida agora? O vencedor é quem tem mais casas marcadas."
ADMIN_USERS = "Usuários"
ADMIN_CARDS = "Cartelas"
RECOVER_SUMMARY = "Salvar link para voltar nesta partida"
RECOVER_HELP = "Se limpar o navegador ou trocar de aparelho, abra este link para continuar como você. Não compartilhe: quem abrir vira você."
RECOVER_LINK = "Meu link pessoal"
ADMIN_NO_USERS = "Ninguém entrou ainda."
ADMIN_NO_SESSION = "Nenhuma partida foi iniciada ainda."
ADMIN_REFRESH = "Atualizar"
ADMIN_COL_RANK = "#"
ADMIN_COL_NAME = "Nome"
ADMIN_COL_MARKED = "Marcadas"
ADMIN_COL_JOINED = "Entrou"
ADMIN_COL_SEEN = "Visto por último"
ADMIN_START = "Iniciar nova partida"
ADMIN_UNMARK = "Desmarcar"
ADMIN_UNMARK_CONFIRM = "Remover esta marcação? O jogador não poderá marcar a casa de novo."
ADMIN_UNBLOCK = "Liberar"
ADMIN_BLOCKED = "bloqueada"
ADMIN_STAR_LINE = "linha completa"        # tooltip of the yellow star: "<n> linha(s) completa(s)"
ADMIN_STAR_LINES = "linhas completas"
ADMIN_STAR_FULL = "cartela completa"
CELL_REVOKED = "O administrador removeu esta marcação: a casa está bloqueada."
ADMIN_START_HELP = "Nenhuma partida em andamento. Ao iniciar, os jogadores poderão entrar."
ADMIN_PHRASES_LINK = "Gerenciar frases"
PH_TITLE = "Frases do bingo"
PH_BACK = "Painel"
PH_ADD_LABEL = "Nova frase (1 a 10 palavras)"
PH_ADD_BUTTON = "Adicionar"
PH_ADD_HELP = "Frases manuais entram com score 99 (máximo) até você editar o score."
PH_SEARCH = "Buscar"
PH_ALL = "todas"
PH_SORT = "Ordenar"
PH_SORTS = {"score": "score", "text": "texto", "dealt": "mais distribuídas", "marked": "mais marcadas",
            "rate": "taxa de marcação", "newest": "mais novas"}
PH_ENABLED_FILTER = {"": "todas", "yes": "ativas", "no": "inativas"}
PH_FILTER = "Filtrar"
PH_TOTAL = "{n} frase(s)"
PH_PAGE = "Página {p} de {n}"
PH_PREV = "‹ Anterior"
PH_NEXT = "Próxima ›"
PH_COLS = ("", "Frase", "Origem", "Score", "Dadas", "Marcadas", "Taxa", "Ativa", "")
PH_YES, PH_NO = "sim", "não"
PH_FROZEN = "fixo"
PH_EDIT, PH_SAVE, PH_CANCEL, PH_DELETE = "Editar", "Salvar", "Cancelar", "Excluir"
PH_DELETE_CONFIRM = "Excluir esta frase? As cartelas já distribuídas não mudam, mas as estatísticas dela se perdem."
PH_EMPTY = "Nenhuma frase encontrada."
PH_ERRORS = {
    "invalid_phrase": "Frase inválida: use de 1 a 10 palavras (até 100 letras), sem cumprimentos.",
    "invalid_score": "Score inválido: use um número entre 0 e 99.",
    "duplicate_phrase": "Essa frase já existe.",
    "not_found": "Frase não encontrada (já foi excluída?).",
    "too_few_enabled": "Precisam sobrar pelo menos 24 frases ativas para montar cartelas.",
}
PH_ADDED = "Frase adicionada com score 99."
PH_SELECT_ALL = "Selecionar todas"
PH_DESELECT_ALL = "Limpar seleção"
PH_DELETE_SELECTED = "Excluir selecionadas"
PH_BULK_CONFIRM = "Excluir as frases selecionadas? As cartelas já distribuídas não mudam, mas as estatísticas delas se perdem."
PH_SELECTED = "{n} selecionada(s)"
PH_BULK_NONE = "Nenhuma frase selecionada."
PH_DELETED_N = "{n} frase(s) excluída(s)."
STREAM_TITLE = "Transmissão ao vivo"
STREAM_SEARCHING = "Procurando a transmissão ao vivo..."
STREAM_OFFLINE = "O canal não está ao vivo agora. Assim que começar, o vídeo aparece aqui."
STREAM_OPEN = "Abrir no YouTube"
STREAM_CHANNEL = "Ver o canal"
OTHERS_HEADING = "Outros jogadores"
OFFLINE_NOTE = "Canal fora do ar agora."
LIVE_HIDE = "Ocultar vídeo e chat"
LIVE_SHOW = "Mostrar vídeo e chat"
VIEW_GRID = "Ver em grade"
VIEW_LIST = "Ver em lista (mais marcadas primeiro)"
CHAT_TITLE = "Chat da live"
CHAT_OFFLINE = "O chat aparece aqui quando a live começar."
PH_ACTIVATE_SELECTED = "Ativar selecionadas"
PH_DEACTIVATE_SELECTED = "Desativar selecionadas"
PH_ACTIVATED_N = "{n} frase(s) ativada(s)."
PH_DEACTIVATED_N = "{n} frase(s) desativada(s)."

# --- Encontre as Falácias
MENU_LABEL = "Jogos"
MENU_BINGO = "Bingo"
MENU_FALACIAS = "Encontre as Falácias"
MENU_RUNNING = "Partida em andamento"

FAL_TITLE = "Encontre as Falácias"
FAL_TAGLINE = "Acompanhe a transcrição e o chat e aponte as falácias lógicas!"
FAL_HOW_TO = "Acompanhe a transcrição (esquerda) e o chat (direita). As linhas novas aparecem sozinhas."
FAL_REPLAY = "Transmissão gravada (replay): as falas aparecem na mesma velocidade da gravação."
FAL_TRANSCRIPT = "Transcrição"
FAL_CHAT = "Chat"
FAL_VIDEO_END = "Fim da transmissão. A partida continua até o administrador encerrar."
FAL_START_HEADING = "Nenhuma partida de Falácias em andamento"
FAL_JOIN_HEADING = "Tem uma partida de Falácias rolando!"
FAL_GAME_OVER = "Fim da partida de Falácias!"
FAL_NO_POINTS = "Ninguém pontuou desta vez."

FAL_PICK_HELP = "Selecione um trecho da transcrição ou do chat, depois escolha a falácia."
FAL_SELECTED = "Trecho selecionado"
FAL_FILTER = "Buscar falácia..."
FAL_SEND = "Enviar"
FAL_CLEAR = "Limpar"
FAL_SENT = "Enviado! Aguarde a confirmação do apresentador."
FAL_SOURCE = "Fonte: Wikipédia (CC BY-SA 4.0)"
FAL_SOURCE_URL = "https://pt.wikipedia.org/wiki/Lista_de_falácias"
FAL_MINE = "Meus envios"
FAL_NO_PAIRS = "Você ainda não enviou nada."
FAL_PENDING = "Aguardando"
FAL_VERDICTS = {"correct": "Correto", "wrong": "Errado", "spam": "Spam"}
FAL_ERRORS = {
    "bad_selection": "Seleção inválida. Selecione o trecho de novo.",
    "selection_too_long": "Trecho grande demais. Selecione um trecho menor.",
    "unknown_fallacy": "Escolha uma falácia da lista.",
    "rate_limited": "Calma! Aguarde {n} s entre envios.",
    "duplicate_mark": "Você já enviou esse trecho com essa falácia.",
    "feed_down": FEED_UNAVAILABLE,
    "generic": "Algo deu errado. Tente de novo.",
}

# ---- admin: Falácias tab
ADMIN_TAB_BINGO = "Bingo"
ADMIN_TAB_FALACIAS = "Encontre as Falácias"
ADMIN_TAB_PHRASES = "Frases do Bingo"
AF_TITLE = "Encontre as Falácias - admin"
AF_NO_SESSION = "Nenhuma partida de Falácias em andamento."
AF_START = "Iniciar partida de Falácias"
AF_FINISH = "Encerrar partida de Falácias"
AF_FINISH_CONFIRM = "Encerrar a partida agora? Depois disso não dá mais para avaliar os envios."
AF_QUEUE = "Fila de envios"
AF_PENDING = "Pendentes"
AF_QUEUE_EMPTY = "Nenhum envio pendente."
AF_SENT_AFTER = "enviado {s} s depois da fala"
AF_CORRECT = "Correto"
AF_WRONG = "Errado"
AF_SPAM = "Spam"
AF_ALL_SAME = "Todas iguais ({n}):"
AF_MESSAGES = "Mensagens para o apresentador"
AF_NO_MESSAGES = "Nenhuma mensagem nova."
AF_MSG_DONE = "Passada"
AF_MSG_HIDE = "Ocultar"
AF_MSG_UNDO = "Desfazer"
AF_MSG_HANDLED = "Tratadas"
AF_BOARD = "Placar"
AF_COL_PLAYER = "Jogador"
AF_COL_POINTS = "Pontos"
AF_COL_PAIRS = "Envios"
AF_COL_PENDING = "Pend."

# ---- player: message box for the host
HM_HEADING = "Mensagem para o apresentador"
HM_HELP = "Escreva uma pergunta ou comentário curto (até 280 letras). O administrador repassa ao vivo."
HM_PLACEHOLDER = "Sua mensagem..."
HM_SEND = "Enviar mensagem"
HM_SENT = "Mensagem enviada!"
HM_COUNT = "Enviadas: {n}/{max}"
HM_ERRORS = {
    "empty_message": "Escreva alguma coisa antes de enviar.",
    "bad_message": "A mensagem tem caracteres inválidos.",
    "message_too_long": "Mensagem longa demais (máximo 280 letras).",
    "message_rate_limited": "Calma! Aguarde {n} s entre mensagens.",
    "message_limit_reached": "Você já enviou o máximo de mensagens ({max}) nesta partida.",
    "generic": "Algo deu errado. Tente de novo.",
}

# ---- Falácias: end screen
FAL_WINNER = "Vencedor(a): {name} com {pts} pontos"
FAL_COL_CORRECT = "Corretos"
FAL_COL_WRONG = "Errados"
FAL_COL_SPAM = "Spam"
FAL_COL_PENDING = "Sem veredito"
FAL_UNREVIEWED_NOTE = "Envios sem veredito do apresentador valem só os 5 pontos do envio."

# ---- Falácias: where the video comes from
SRC_VIDEO = "Vídeo da partida (replay)"
SRC_TITLE_UNKNOWN = "título indisponível"
SRC_LIVE_NOW = "Ao vivo agora"
SRC_LIVE_VERTICAL = "Transmissão ao vivo na vertical, não usada"
SRC_LIVE_OFFLINE = "O canal não está ao vivo agora."
SRC_ONLY_HORIZONTAL = "Só transmissões na horizontal são usadas."

# ---- Falácias: live source (L4)
FAL_REPLAY_MISSING = "O vídeo {id} ainda não foi importado. No servidor, rode: uv run corpus replay-import {id}"
SRC_SESSION_LIVE = "Transmissão ao vivo da partida"
FAL_NO_TRANSCRIPT = (
    "Estamos ainda sem transcrição ao vivo.\n"
    "Digite abaixo a frase que você ouviu e escolha a falácia. Se acertar vc acumulará pontos.\n\n"
    "A transcrição automática será implementada em breve. Fique ligado."
)
FAL_CHAT_DOWN = "Chat ao vivo fora do ar. Tentando reconectar..."
FAL_LIVE_HOW_TO = "Acompanhe o chat (direita) na transmissão ao vivo. Frases faladas: digite-as na caixa abaixo."
FAL_SPOKEN_HEADING = "Frase falada"
FAL_SPOKEN_HELP = "Ouviu uma falácia na transmissão? Digite a frase (até 300 caracteres) e escolha a falácia."
FAL_SPOKEN_PLACEHOLDER = "O que foi dito..."
FAL_SPOKEN_SEND = "Enviar frase"
FAL_SPOKEN_FALLACY = "Falácia"
FAL_SPOKEN_ERRORS = {
    "bad_selection": "Digite a frase falada.",
    "spoken_too_long": "Frase grande demais (máximo 300 caracteres).",
    "unknown_fallacy": FAL_ERRORS["unknown_fallacy"],
    "rate_limited": FAL_ERRORS["rate_limited"],
    "duplicate_mark": FAL_ERRORS["duplicate_mark"],
    "generic": FAL_ERRORS["generic"],
}
AF_TYPED = "digitada"
AF_VIDEO_FIELD = "Link do vídeo gravado (replay), se o canal não estiver ao vivo"

# ---- admin: replay catalogue (on /admin/falacias)
AF_NO_SOURCE = "O canal não está ao vivo na horizontal. Escolha um replay pronto na lista abaixo."
AF_REPLAYS = "Replays disponíveis"
AF_REPLAY_HELP = ("Cole o link de uma transmissão já encerrada (horizontal, até 12 horas). O servidor baixa a transcrição e o chat; "
                  "o replay só pode ser usado quando ficar pronto.")
AF_REPLAY_ADD_LABEL = "Link do vídeo (YouTube)"
AF_REPLAY_ADD = "Coletar"
AF_REPLAY_EMPTY = "Nenhum replay ainda."
AF_REPLAY_COLS = ("Vídeo", "Duração", "Linhas (fala / chat)", "Estado", "")
AF_REPLAY_STATUS = {"queued": "Na fila", "downloading": "Baixando", "importing": "Importando", "ready": "Pronto", "failed": "Falhou"}
AF_REPLAY_START = "Iniciar com este replay"
AF_REPLAY_RETRY = "Tentar de novo"
AF_REPLAY_DELETE = "Excluir"
AF_REPLAY_DELETE_CONFIRM = "Excluir este replay e todas as suas linhas?"
AF_REPLAY_ERRORS = {
    "invalid_video": ERRORS["invalid_video"],
    "already_ready": "Este vídeo já está na lista e pronto para uso.",
    "already_running": "Este vídeo já está na fila ou sendo coletado.",
    "queue_full": "A fila está cheia (máximo 5 vídeos aguardando). Espere algum terminar.",
    "busy": "Este vídeo está na fila ou sendo coletado agora. Espere terminar.",
    "in_use": "Uma partida em andamento usa este replay. Encerre a partida primeiro.",
    "not_found": "Replay não encontrado (já foi excluído?).",
    "not_ready": "Este replay ainda não está pronto.",
    "session_active": "Já há uma partida de Falácias em andamento.",
    "bad_state": "O estado do replay mudou. Atualize a página.",
    "generic": ERRORS["generic"],
}

# ---- admin: fallacy list
ADMIN_TAB_FALLACIES = "Lista de falácias"
ADMIN_GROUP_GAMES = "Partidas"
ADMIN_GROUP_CONTENT = "Conteúdo"
ADMIN_UPDATED = "atualizado agora"
ADMIN_ELAPSED = "Tempo"
AF_ONLY_PENDING = "Apenas pendentes"
AF_SHORTCUTS = "Atalhos: C correto · E errado · S spam (Shift = todas iguais). Atuam no item destacado."
FA_TITLE = "Lista de falácias"
FA_ADD_LABEL = "Nova falácia"
FA_ADD_HELP = "Formato: Nome em português (English name - Nome em latim). Use nomes curtos; o latim só quando existir."
FA_NAME, FA_CATEGORY, FA_DESCRIPTION = "Nome", "Categoria", "Descrição"
FA_ACTIVE = "Ativa"
FA_ADD_BUTTON = "Adicionar"
FA_TOTAL = "{n} falácias"
FA_EMPTY = "Nenhuma falácia encontrada."
FA_COLS = ("Nome", "Categoria", "Origem", "Usos", "Ativa", "")
FA_SOURCES = {"": "todas as origens", "manual": "manual", "wikipedia": "wikipedia"}
FA_ENABLED_FILTER = {"": "todas", "1": "ativas", "0": "inativas"}
FA_DELETE_CONFIRM = "Excluir esta falácia? Só é possível se nenhum jogador a usou."
FA_ADDED = "Falácia adicionada."
FA_ERRORS = {
    "invalid_name": "Nome inválido: use de 1 a 120 caracteres.",
    "invalid_category": "Categoria inválida: use de 1 a 60 caracteres.",
    "invalid_description": "Descrição muito longa (máximo 1000 caracteres).",
    "name_taken": "Já existe uma falácia com esse nome.",
    "in_use": "Esta falácia já foi usada em partidas: desative-a em vez de excluir.",
    "too_few_enabled": "Pelo menos 5 falácias precisam ficar ativas.",
    "not_found": "Falácia não encontrada.",
}
FA_ATTRIBUTION = "Descrições das falácias da Wikipédia: colaboradores da Wikipédia, CC BY-SA 4.0."
