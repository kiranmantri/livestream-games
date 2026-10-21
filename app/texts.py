"""All user-facing strings (pt-BR: the audience is the Brazilian stream). Change here to translate."""

TITLE = "Bingo da Live"
TAGLINE = "Marque as expressões que aparecerem na live!"
START_HEADING = "Nenhuma partida em andamento"
START_HELP = "Você será o primeiro: digite seu nome e comece a partida."
START_BUTTON = "Começar partida"
JOIN_HEADING = "Tem uma partida rolando!"
JOIN_HELP = "{n} jogador(es) na partida. Escolha um nome para entrar."
JOIN_BUTTON = "Entrar na partida"
NAME_LABEL = "Seu nome (máx. 20 letras)"
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
    "ip_limit_reached": "Já há jogadores demais entrando desta rede (limite {n}).",
    "session_already_active": "Alguém acabou de começar a partida. Entre nela!",
    "no_active_session": "A partida acabou ou ainda não começou.",
    "no_phrases": "Não foi possível montar sua cartela (poucas expressões cadastradas).",
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
