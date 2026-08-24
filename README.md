# 🤖 Bot de Vagas → Telegram

Busca vagas de estágio/júnior em tecnologia recém-publicadas, a cada hora,
e envia as novas direto pro seu Telegram. Roda de graça no GitHub Actions —
sem servidor.

**Fontes ativas por padrão:** Gupy, LinkedIn, Vagas.com.br, InfoJobs,
Catho e Programathor.
**Fontes opcionais** (agregadores que cobrem Indeed e dezenas de outros
sites — basta cadastrar uma chave gratuita): Adzuna e Jooble.

## Como colocar no ar (~10 min)

### 1. Pegue seu chat_id do Telegram
1. No Telegram, procure **@userinfobot** e envie qualquer mensagem.
2. Ele responde com o seu **Id** (um número tipo `123456789`). Guarde-o.
3. Importante: abra também o chat do **seu bot** (criado no @BotFather) e
   envie um "oi" — isso autoriza o bot a te mandar mensagens.

### 2. Crie o repositório no GitHub
1. Crie uma conta em [github.com](https://github.com) (se ainda não tiver).
2. Clique em **New repository** → nome: `vagas-telegram-bot` →
   marque **Private** → **Create repository**.
3. Envie todos os arquivos deste projeto para o repositório
   (**Add file → Upload files**, arraste tudo — inclusive a pasta
   `.github/workflows/`; se o upload da pasta não funcionar pelo site,
   crie o arquivo manualmente em **Add file → Create new file** com o nome
   `.github/workflows/vagas.yml` e cole o conteúdo).

### 3. Configure os segredos
No repositório: **Settings → Secrets and variables → Actions →
New repository secret**. Crie dois:

| Nome | Valor |
|------|-------|
| `TELEGRAM_TOKEN` | o token do @BotFather (ex.: `1234567890:AAH...`) |
| `TELEGRAM_CHAT_ID` | o número que o @userinfobot te deu |

**Opcionais** (para ligar os agregadores, que cobrem Indeed e muitos
outros sites):

| Nome | Onde conseguir (grátis) |
|------|--------------------------|
| `ADZUNA_APP_ID` e `ADZUNA_APP_KEY` | cadastro em [developer.adzuna.com](https://developer.adzuna.com) |
| `JOOBLE_KEY` | cadastro em [jooble.org/api/about](https://jooble.org/api/about) |

### 4. Ative e teste
1. Vá na aba **Actions** do repositório e habilite os workflows se pedido.
2. Clique em **Bot de vagas → Run workflow** para rodar na hora.
3. Em ~1 minuto as primeiras vagas chegam no seu Telegram. 🎉

A partir daí ele roda **sozinho a cada hora**, e só envia vaga que nunca
enviou antes (o histórico fica no arquivo `seen.json`, atualizado
automaticamente).

## Personalizar as buscas

Edite o `config.json`:

- `buscas` — termos pesquisados na Gupy, LinkedIn, InfoJobs, Adzuna e Jooble.
- `buscas_vagas_com` / `buscas_catho` — termos no formato de URL desses
  sites (palavras separadas por hífen).
- `buscas_programathor` — filtros do Programathor (júnior e estágio).
- `localizacao_linkedin` — região da busca no LinkedIn.
- `janela_horas` — só envia vagas publicadas nas últimas N horas (padrão 3).
- `excluir_no_titulo` / `nunca_excluir_se_tiver` — filtros de senioridade.
- `max_vagas_por_execucao` — teto de mensagens por rodada (padrão 20).

## Dicas

- **Vagas Gupy**: a busca cobre o Brasil todo; o bot filtra para
  remoto ou São Paulo automaticamente.
- **LinkedIn**: usa o endpoint público de busca; se o LinkedIn limitar
  temporariamente (erro 429), o bot apenas pula e tenta na próxima hora.
- **Repositório parado**: o GitHub desativa agendamentos após ~60 dias sem
  atividade — como o bot faz um commit por rodada, isso não deve acontecer;
  se um dia parar, é só rodar manualmente pela aba Actions.
- **Currículo por vaga**: quando uma vaga te interessar, cole o link dela
  na conversa com o Claude e peça a versão adaptada do seu currículo.
