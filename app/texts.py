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
    "kicked": "O administrador removeu você desta partida e sua cartela foi anulada. Você pode entrar de novo com uma cartela nova.",
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
MYLINK_COPIED = "Link copiado para a área de transferência!"
MYLINK_FAILED = "Não foi possível copiar. Segure o ícone e copie o endereço do link."
MYLINK_TITLE = ("Meu link pessoal: salve ou abra em outro aparelho para voltar às suas partidas. "
                "Não compartilhe: quem abrir vira você.")
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
CELL_LOCKED = "Confirmada pelo administrador: esta marcação não pode ser desfeita."
ADMIN_MARKS = "Marcações"
ADMIN_MARKS_HELP = ("Confira cada frase: Correto vale para TODOS (trava a casa de quem marcou, e quem marcar depois já fica "
                    "confirmado); Incorreto limpa a frase de todos (podem marcar de novo depois).")
ADMIN_MARKS_NONE = "Nenhuma casa marcada ainda."
ADMIN_MARKS_PENDING = "{n} a conferir"
ADMIN_MARK_OK = "Correto"
ADMIN_MARK_WRONG = "Incorreto"
ADMIN_MARK_PENDING = "a conferir"
ADMIN_MARK_CONFIRMED = "confirmada"
ADMIN_COL_PLAYER = "Jogador"
ADMIN_COL_PLAYERS = "Quem marcou"
ADMIN_COL_PHRASE = "Frase"
ADMIN_COL_AT = "Marcada às"
ADMIN_COL_STATUS = "Situação"
SWAP_OFFER = "🎁 Você ganhou uma troca de cartela!"
SWAP_HELP = ("Escolha com quem trocar: você fica com a cartela dele(a), com as marcações, e ele(a) com a sua. "
             "A outra pessoa não pode recusar. Vale até o fim da partida.")
SWAP_PICK = "Trocar com"
SWAP_BUTTON = "Trocar de cartela"
SWAP_CONFIRM = "Trocar de cartela agora? Não dá para desfazer."
SWAP_DECLINE = "Não quero trocar"
SWAP_NOBODY = "Ninguém pode receber a troca agora. Ela continua valendo até o fim da partida."
SWAP_DONE = "🔄 Você trocou de cartela com {name} às {at}."
SWAP_RECEIVED = "🔄 {name} trocou de cartela com você às {at}!"
SWAP_ERRORS = {
    "not_eligible": "Não dá para trocar com essa pessoa. Escolha outra.",
    "used": "Essa troca já foi usada.",
    "not_yours": "Essa troca não é sua.",
    "not_active": "A partida acabou.",
}
ADMIN_KW = "🔑 Palavras-chave (secretas)"
ADMIN_KW_HELP = ("Só você vê. Escolha até 3 frases das cartelas desta partida. Quando você confirma (Correto) a marcação de uma "
                 "palavra-chave, o jogador ganha UMA troca de cartela com quem quiser (menos quem também marcou essa palavra).")
ADMIN_KW_ADD = "Adicionar"
ADMIN_KW_PICK = "Frase das cartelas"
ADMIN_KW_REMOVE = "Remover"
ADMIN_KW_FROZEN = "travada (já tem marcação confirmada)"
ADMIN_KW_NONE = "Nenhuma palavra-chave escolhida."
ADMIN_KW_CARDS = "{n} cartela(s)"
ADMIN_KW_LOG = "Trocas"
ADMIN_KW_EARNED = "{holder} ganhou uma troca ({text})"
ADMIN_KW_OPEN = "disponível"
ADMIN_KW_USED = "trocou com {target} às {at}"
ADMIN_KW_DECLINED = "recusou"
ADMIN_KW_ERRORS = {
    "max": "Já há 3 palavras-chave.",
    "frozen": "Essa palavra-chave já tem marcação confirmada: não pode mais ser removida.",
    "not_on_cards": "Essa frase não está em nenhuma cartela desta partida.",
    "not_active": "A partida acabou.",
}
ADMIN_KICK = "Expulsar"
ADMIN_KICK_CONFIRM = "Expulsar {name}? A cartela dele(a) será anulada; ele(a) poderá entrar de novo com uma cartela nova."
ADMIN_START_HELP = "Nenhuma partida em andamento. Ao iniciar, os jogadores poderão entrar."
ADMIN_PHRASES_LINK = "Gerenciar frases"
ADMIN_IPLIMIT_LABEL = "Jogadores por rede (IP), nos dois jogos"
ADMIN_IPLIMIT_SAVE = "Salvar limite"
ADMIN_IPLIMIT_HELP = "Vale a partir da próxima entrada; quem já entrou não é removido. De 1 a 50."
ADMIN_IPLIMIT_BAD = "Limite inválido: use um número inteiro de 1 a 50."
PH_TITLE = "Frases do bingo"
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
VIDEO_HIDE = "Ocultar vídeo"
VIDEO_SHOW = "Mostrar vídeo"
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

# --- Falaciômetro
MENU_LABEL = "Jogos"
MENU_BINGO = "Bingo"
MENU_FALACIAS = "Falaciômetro"
MENU_BINGO_SUB = "Marque o que ouvir"
MENU_FALACIAS_SUB = "Aponte a falácia"
USER_CHANGE = "Trocar nome"
MENU_RUNNING = "Partida em andamento"

FAL_TITLE = "Falaciômetro"
FAL_TAGLINE = "Acompanhe a transcrição e o chat e aponte as falácias lógicas!"
FAL_HOW_TO = "Acompanhe a transcrição (esquerda) e o chat (direita). As linhas novas aparecem sozinhas."
FAL_REPLAY = "Transmissão gravada (replay): as falas aparecem na mesma velocidade da gravação."
FAL_TRANSCRIPT = "Transcrição"
FAL_CHAT = "Chat"
FAL_VIDEO_END = "Fim da transmissão. A partida continua até o administrador encerrar."
FAL_START_HEADING = "Nenhuma partida de Falácias em andamento"
FAL_JOIN_HEADING = "Tem uma partida de Falácias rolando!"
FAL_GAME_OVER = "Fim da partida de Falácias!"

FAL_PICK_HELP = "Selecione um trecho da transcrição ou do chat, depois escolha a falácia."
FAL_SELECTED = "Enviar ao apresentador"
FAL_FILTER = "Buscar falácia..."
FAL_SEND = "Enviar"
FAL_CLEAR = "Limpar"
FAL_SENT = "Enviado! Aguarde a confirmação do apresentador."
FAL_SOURCE = "Fonte: Wikipédia (CC BY-SA 4.0)"
FAL_SOURCE_URL = "https://pt.wikipedia.org/wiki/Lista_de_falácias"
FAL_NO_PAIRS = "Você ainda não enviou nada."
FAL_NO_OTHERS = "Os outros participantes não enviaram nada."
FAL_PENDING = "Aguardando"
FAL_VERDICTS = {"correct": "Correto", "wrong": "Errado", "spam": "Spam"}
FAL_ERRORS = {
    "bad_selection": "Seleção inválida. Selecione o trecho de novo.",
    "selection_too_long": "Trecho grande demais. Selecione um trecho menor.",
    "unknown_fallacy": "Escolha uma falácia da lista.",
    "rate_limited": "Calma! Aguarde {n} s entre envios.",
    "duplicate_mark": "Você já enviou esse trecho com essa falácia.",
    "feed_down": FEED_UNAVAILABLE,
    "need_fallacy": "Escolha uma falácia para o trecho selecionado (ou limpe a seleção para enviar só a mensagem).",
    "need_text": "Selecione um trecho no chat ou na transcrição, ou escreva a frase na caixa de mensagem.",
    "empty": "Escreva uma mensagem, ou selecione um trecho e escolha uma falácia.",
    "note_too_long": "Mensagem longa demais (máximo 280 caracteres).",
    "generic": "Algo deu errado. Tente de novo.",
}
FAL_NOTE_LABEL = "Texto"
FAL_NOTE_PLACEHOLDER = ("Clique no chat ou na transcrição para trazer o trecho até aqui. Você pode editar o texto, ou escrever a frase "
                        "que foi dita (se o chat ou a transcrição falhou). Sem falácia escolhida, o texto vai como mensagem ao apresentador.")
FAL_NO_SELECTION = "Nenhum trecho selecionado. Clique ou selecione texto no chat ou na transcrição, ou escreva abaixo."
FAL_SENT_MESSAGE = "Mensagem enviada ao apresentador!"
AF_NOTE = "Mensagem do jogador"

# ---- admin: Falácias tab
ADMIN_TAB_BINGO = "Bingo"
ADMIN_TAB_FALACIAS = "Falaciômetro"
ADMIN_TAB_PHRASES = "Frases do Bingo"
AF_TITLE = "Falaciômetro - admin"
AF_NO_SESSION = "Nenhuma partida de Falácias em andamento."
AF_START = ADMIN_START
AF_URL_LABEL = "Ou cole o link de um vídeo ou live do YouTube (qualquer canal)"
AF_URL_PLACEHOLDER = "https://www.youtube.com/watch?v=..."
AF_URL_START = "Iniciar"
AF_URL_CANCEL = "Cancelar"
AF_PICK_LABEL = "Escolher um replay já coletado"
AF_PICK_NONE = "— nenhum —"
AF_URL_QUEUED = "Esse vídeo já terminou. Coloquei o replay na fila de coleta (fica guardado para as próximas vezes). Quando ele ficar pronto, clique em Iniciar nova partida de novo e escolha-o na lista."
AF_URL_COLLECTING = "O replay desse vídeo já está na fila ou sendo coletado. Quando ficar pronto, ele aparece na lista de replays."
AF_URL_HELP = "Live horizontal: acompanha a live. Vídeo já encerrado: o replay é coletado e guardado; quando ficar pronto, escolha-o na lista. Se você escolher e colar, vale o link."
AF_URL_ERRORS = {
    "empty": "Escolha um replay na lista ou cole um link do YouTube.",
    "invalid": "Link inválido. Cole o link de um vídeo ou live do YouTube (youtube.com/watch?v=..., youtu.be/... ou /live/...).",
    "lookup": "Não consegui consultar esse vídeo no YouTube (privado, removido ou YouTube fora do ar). Tente de novo.",
    "vertical": "Esse vídeo é vertical (celular). Só vídeos horizontais são usados.",
    "upcoming": "Essa live ainda não começou. Inicie a partida quando ela estiver no ar.",
    "processing": "O YouTube ainda está processando esse vídeo. Aguarde e tente de novo.",
    "not_imported": "Esse vídeo já terminou e o replay ainda não foi coletado. Assim que ele ficar pronto, aparece na lista de replays desta janela.",
}
AF_FINISH = ADMIN_FINISH
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

# ---- Falácias: live source (L4)
FAL_REPLAY_MISSING = "O vídeo {id} ainda não foi importado. No servidor, rode: uv run corpus replay-import {id}"
SRC_SESSION_LIVE = "Transmissão ao vivo da partida"
FAL_NO_TRANSCRIPT = (
    "Estamos ainda sem transcrição ao vivo.\n"
    "Clique aqui, escreva no painel de envio a frase que você ouviu e escolha a falácia. Se acertar, você acumula pontos.\n\n"
    "A transcrição automática será implementada em breve. Fique ligado."
)
FAL_CHAT_DOWN = "Chat ao vivo fora do ar. Tentando reconectar..."
FAL_STT_LABEL = "Transcrição automática, pode conter erros"
FAL_STT_DOWN = "A transcrição automática está fora do ar. Tentando reconectar... Enquanto isso, clique no chat ou na transcrição e escreva a frase no painel de envio."
FAL_LIVE_HOW_TO_STT = ("Acompanhe a transmissão, o chat e a transcrição automática (da esquerda para a direita). Selecione o trecho com a falácia. "
                       "Se a transcrição errar ou perder algo, escreva a frase no painel de envio.")
FAL_LIVE_HOW_TO = "Acompanhe a transmissão e o chat ao vivo. Frases faladas: clique no chat e escreva a frase no painel de envio."
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
ADMIN_BRAND = "Admin"
ADMIN_VIEW_SITE = "Ver o site"
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

OR = "ou"

# --- Verdade ou Lorota (fact-checking game)
MENU_CHECKER = "Verdade ou Lorota"
MENU_CHECKER_SUB = "Dê nota à frase"
CK_TITLE = "Verdade ou Lorota"
CK_TAGLINE = "Alguém disse algo na live. É verdade ou é lorota? Dê o seu palpite, de -5 (💩) a +5 (⭐), e veja quem chega mais perto da nota certa."
CK_NONE_HEADING = "Nenhuma partida agora"
CK_NONE_HELP = "Quando o apresentador iniciar uma partida, ela aparece aqui. Deixe esta página aberta."
CK_RUNNING_HEADING = "Tem uma partida rolando!"
ADMIN_TAB_CHECKER = "Verdade ou Lorota"
CK_ADMIN_TITLE = "Verdade ou Lorota - admin"
CK_NO_SESSION = "Nenhuma partida de “Verdade ou Lorota” em andamento."
CK_URL_LABEL = "Link do vídeo ou da live do YouTube (opcional)"
CK_URL_HELP = "Em branco: partida sem vídeo (você pode dar as notas na mesma). Live horizontal: acompanha a live. Vídeo encerrado: precisa ter o replay já coletado (aba Falaciômetro)."
CK_URL_ERRORS = AF_URL_ERRORS | {
    "not_imported": "Esse vídeo já terminou e o replay ainda não foi coletado. Colete-o primeiro, na aba Falaciômetro.",
}
CK_AUTO_ON = "Verificação automática ligada: {model}"
CK_AUTO_OFF = "Verificação automática desligada ({reason}). As frases ficam na fila e você dá as notas."
CK_CHECKS = "Verificações"
CK_PENDING = "Pendentes"
CK_SHOW_ALL = "Mostrar todas"
CK_SHOW_PENDING = "Mostrar só as pendentes"
CK_NO_CHECKS = "Nenhuma verificação pendente."
CK_STATUS = {"queued": "Na fila", "running": "Verificando", "done": "Verificada", "needs_admin": "Aguardando você", "resolved": "Resolvida"}
CK_VERDICTS = {"true": "Verdadeira", "partial": "Parcial", "false": "Falsa", "unverifiable": "Não verificável"}
CK_CONFIDENCE = {"low": "confiança baixa", "medium": "confiança média", "high": "confiança alta"}
CK_AI = "IA"
CK_GUESSES = "Palpites"
CK_SOURCES = "Fontes"
CK_ERROR = "Erro"
CK_SCORE_LABEL = "Nota"
CK_SET_SCORE = "Definir nota"
CK_DISMISS = "Ninguém pontua"
CK_DISMISSED = "Descartada pelo apresentador"
CK_FINISH_CONFIRM = "Encerrar a partida agora? Depois disso não dá mais para dar notas."
CK_COL_EXACT = "Na mosca"
CK_COL_CLAIMS = "Frases"
CK_CLAIMS_N = "{n} envio(s)"
CK_COL_POINTS = "Pontos"

# --- admin shell v2: one session for the three games
ADMIN_SESSION_START = "Iniciar sessão"
ADMIN_SESSION_END = "Finalizar sessão"
ADMIN_SESSION_END_CONFIRM = "Finalizar a sessão agora? Os três jogos em andamento (Bingo, Falaciômetro e Verdade ou Lorota) terminam juntos."
ADMIN_SESSION_VIDEO = "Vídeo"
ADMIN_SUB_GAME = "Jogo"
ADMIN_SUB_CONTENT = "Conteúdo"
SESSION_NONE_HINT = "Nenhuma partida em andamento. Use “Iniciar sessão”, no alto da página, para começar os três jogos de uma vez."
SESSION_DIALOG_HELP = "Um só vídeo vale para os três jogos. Live horizontal: acompanha a live. Vídeo já encerrado: o replay é coletado e guardado; quando ficar pronto, escolha-o na lista."

# ---- Verdade ou Lorota: the player's game page (FC4)
CK_JOIN_HEADING = "Entrar na partida"
CK_HOW_TO = ("Clique numa frase do chat ou da transcrição (ou escreva uma). Escolha a sua nota, de -5 (💩 mentira feia) a +5 (⭐ verdade "
             "de ponta a ponta), e envie. A IA confere na internet e quem chegar mais perto da nota certa ganha mais pontos.")
CK_AUTO_LABEL = "Verificação automática, pode conter erros"
CK_PANEL_BAR = "Enviar uma frase"
CK_GUESS_LABEL = "Sua nota"
CK_NOTE_LABEL = "Frase"
CK_NOTE_PLACEHOLDER = ("Clique no chat ou na transcrição para trazer a frase até aqui. Você pode editar o texto, ou escrever o que foi "
                       "dito (se o chat ou a transcrição falhou).")
CK_SEND = "Enviar"
CK_SENT = "Frase enviada! Já estamos conferindo."
CK_MINE = "Minhas frases"
CK_OTHERS = "Outros participantes"
CK_NO_OTHERS = "Os outros participantes ainda não enviaram nenhuma frase."
CK_NO_CLAIMS = "Você ainda não enviou nenhuma frase."
CK_STATE_WAITING = "Verificando…"
CK_STATE_ADMIN = "Aguardando o apresentador"
CK_STATE_DISMISSED = "Descartada pelo apresentador"
CK_YOUR_GUESS = "Seu palpite"
CK_FINAL = "Nota final"
CK_POINTS = "{n} pts"
CK_GAME_OVER = "Fim da partida!"
CK_WINNER = "Vencedor(a): {name} com {pts} pontos"
CK_COL_PLAYER = "Jogador"
CK_ERRORS = {
    "bad_guess": "Escolha a sua nota, de -5 a +5.",
    "bad_selection": "Seleção inválida. Selecione o trecho de novo.",
    "selection_too_long": "Trecho grande demais. Selecione um trecho menor.",
    "spoken_too_long": "Frase longa demais (máximo 300 caracteres).",
    "note_too_long": "Frase longa demais (máximo 280 caracteres).",
    "rate_limited": "Calma! Aguarde {n} s entre frases.",
    "claim_limit_reached": "Você já enviou o máximo de {max} frases nesta partida.",
    "duplicate_claim": "Você já enviou essa frase.",
    "empty": "Selecione um trecho ou escreva uma frase.",
    "feed_down": FAL_ERRORS["feed_down"],
    "generic": "Algo deu errado. Tente de novo.",
}

# ---- score line + modal (both video games)
SCORE_NOW = "Placar atual"
SCORE_CLOSE = "Fechar"
POINTS_N = "{n} pontos"

# ---- /admin/checker table
CK_TH_CLAIM = "Alegação"
CK_TH_POINTS = "Pontos"
CK_LINE_GUESS = "Palpite"
CK_CARDS_TITLE = "Frases dos jogadores"
CK_CARDS_HINT = "Clique nos jogadores do placar para ver só as frases deles (pode escolher vários)."
CK_CARDS_ALL = "Mostrar todos"
CK_CARDS_NONE = "Os jogadores escolhidos ainda não enviaram frases."
CK_CARDS_EMPTY = "Nenhuma frase enviada ainda."
CK_CARDS_ROW_TITLE = "Clique para mostrar ou esconder as frases deste jogador"
CK_LINE_AI = "IA"
CK_LINE_HOST = "Host"
CK_EDIT = "Editar"
CK_RECHECK = "Verificar Alegação"
CK_HOST_EDIT = "Clique para definir o score do host"
CK_TH_HOST_SCORE = "Score do host"
CK_TH_AI_CHECK = "Verificação da IA"
CK_TH_ACTIONS = "Ações"
CK_SEARCH_LABEL = "Buscar nas verificações"
CK_SEARCH_PLACEHOLDER = "Buscar por jogador, alegação, verificação…"
CK_SEARCH_BTN = "Buscar"
CK_NO_MATCH = "Nenhuma verificação encontrada para essa busca."
