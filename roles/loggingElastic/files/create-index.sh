#!/bin/bash
#
# create-index.sh — per ogni namespace di un elenco crea, su Elasticsearch,
# l'index template, l'indice di partenza -000001 e l'alias di scrittura.
#
#   ./create-index.sh https 10.10.111.24 elastic 'WcyUYmmLcStgJnZ7GsK*' \
#       k8s-coll-app /root/logging-script/app_namespaces.txt 500 1 1 logs
#
# LA PASSWORD VA FRA APICI SINGOLI. Contiene un "*": senza apici la shell prova
# a espanderlo come nome di file PRIMA che lo script lo veda, e se in quel
# momento la directory contiene un file che combacia, allo script arriva una
# password diversa da quella scritta. Vale anche per $, ?, [ e lo spazio.
#
# SCRITTO DAL RUOLO loggingElastic — le modifiche fatte qui a mano vengono
# sovrascritte al prossimo lancio di playbooks/70-logging.yml.
#
# Il ruolo fa gia' tutto questo da solo, senza passare da qui, e calcola le
# priorita' invece di scoprirle: questo script serve per i giri a mano.
#
# --- COSA CAMBIA RISPETTO ALLA VERSIONE DI PARTENZA ------------------------
#
# 1. Non tocca piu' $HOME. La versione di partenza fa "HOME=/root/elk-scripts" e
#    ci scrive dentro sia il file di appoggio della risposta sia il file dei
#    rilanci. Se quella directory non esiste — e lanciando da
#    /root/logging-script non esiste — "tee" fallisce, il file della risposta
#    non viene creato, il grep che cerca il conflitto di priorita' fallisce
#    anche lui, e lo script prende sempre il ramo "nessun conflitto": crea
#    l'indice anche quando il template e' stato RIFIUTATO, e l'indice nasce
#    senza template e quindi senza ILM.
#    Qui i file di appoggio stanno accanto all'elenco che si sta elaborando, e
#    il file dei rilanci si chiama <prefisso>-template-priority-<N>.txt — con
#    ".txt", che e' il nome che si usa poi per rilanciare.
#
# 2. Guarda il codice HTTP, non una frase nel corpo. La versione di partenza
#    riconosce solo il conflitto di priorita': un 401, un host irraggiungibile,
#    un JSON rifiutato passano inosservati e l'indice viene creato lo stesso.
#
# 3. Il file dei rilanci viene azzerato all'inizio. Prima si accumulavano i
#    namespace di tutti i lanci, ripetuti.
#
# 4. Righe vuote e ritorni a capo di Windows vengono saltati. Una riga vuota
#    diventava un template "<prefisso>-" con pattern "<prefisso>-*" a priorita'
#    500: un jolly che si mette davanti a tutti gli altri.
#
# 5. Indice e alias in una chiamata sola, e solo se l'alias non c'e' gia'.
#    La versione di partenza rifa' sempre la PUT dell'indice e ri-aggiunge
#    l'alias con is_write_index: true. Dopo il primo rollover l'indice di
#    scrittura e' il -000002, e quella richiesta prova a riportarlo sul -000001:
#    Elasticsearch la rifiuta, lo script non guarda e prosegue.
#
# 6. Si ferma se la policy ILM non esiste. Il template la nomina ma nessuno la
#    crea: senza, il rollover non avviene mai e l'indice -000001 cresce per
#    sempre. E' l'unico difetto che non da' nessun sintomo il giorno del lancio.
#
# 7. Esce con un codice sensato: 0 tutto a posto, 1 qualcosa e' fallito,
#    2 parametri sbagliati, 3 restano namespace da rilanciare a priorita' piu'
#    alta. Prima usciva sempre 0, anche stampando le istruzioni d'uso.

set -uo pipefail

if [ "$#" -ne 10 ]; then
  cat >&2 <<'USO'
Fornire i seguenti 10 parametri:

  1  http oppure https
  2  ELASTIC_HOST                indirizzo del nodo (porta 9200; per
                                 un'altra porta: "indirizzo:porta")
  3  USER                        utente Elasticsearch
  4  PASSWORD                    FRA APICI SINGOLI se contiene * $ ? [ o spazi
  5  PREFISSO                    es. k8s-coll-app
  6  lista indici                percorso assoluto del file
  7  priorita' template          es. 500
  8  number_of_shards            es. 1
  9  number_of_replicas          es. 1
 10  parte variabile del nome del lifecycle    es. logs

Esempio:
  ./create-index.sh https 10.10.111.24 elastic 'WcyUYmmLcStgJnZ7GsK*' \
      k8s-coll-app /root/logging-script/app_namespaces.txt 500 1 1 logs
USO
  exit 2
fi

HTTP=$1
ELASTIC_HOST=$2
ES_USER=$3
ES_PASSWORD=$4
PREFISSO=$5
LISTA_INDICI=$6
PRIORITY=$7
SHARD=$8
REPLICA=$9
LIFECYCLE_END=${10}

# La porta e' la 9200, come nella versione di partenza. Passando
# "indirizzo:porta" come secondo parametro si usa quella: serve quando
# Elasticsearch non sta sulla porta canonica, e alla versione di partenza
# mancava del tutto.
case "$ELASTIC_HOST" in
  *:*) DESTINAZIONE="$ELASTIC_HOST" ;;
  *)   DESTINAZIONE="$ELASTIC_HOST:9200" ;;
esac
BASE="$HTTP://$DESTINAZIONE"
LIFECYCLE="$PREFISSO-$LIFECYCLE_END"
LAVORO=$(dirname -- "$LISTA_INDICI")
ORDINATA="$LISTA_INDICI.sorted"
PRIORITA_SUCCESSIVA=$((PRIORITY + 100))
FILE_RILANCIO="$LAVORO/$PREFISSO-template-priority-$PRIORITA_SUCCESSIVA.txt"

echo "###################################################"
echo "HTTP           = $HTTP"
echo "ELASTIC_HOST   = $DESTINAZIONE"
echo "USER           = $ES_USER"
echo "PASSWORD       = (${#ES_PASSWORD} caratteri)"
echo "PREFISSO       = $PREFISSO"
echo "LISTA_INDICI   = $LISTA_INDICI"
echo "PRIORITY       = $PRIORITY"
echo "SHARD          = $SHARD"
echo "REPLICA        = $REPLICA"
echo "LIFECYCLE      = $LIFECYCLE"
echo "###################################################"

if [ ! -r "$LISTA_INDICI" ]; then
  echo "ERRORE: $LISTA_INDICI non esiste o non e' leggibile." >&2
  exit 2
fi

case "$HTTP" in
  http|https) ;;
  *) echo "ERRORE: il primo parametro deve essere http o https, non \"$HTTP\"." >&2; exit 2 ;;
esac

TEMP=$(mktemp -d)
trap 'rm -rf "$TEMP"' EXIT

# CODICE e CORPO restano valorizzati dopo ogni chiamata.
CODICE=""
CORPO=""

chiama() {   # chiama <metodo> <url> [corpo json]
  local metodo=$1 url=$2 corpo=${3-}
  local risposta

  # L'errore di curl va in un file suo e non mescolato alla risposta: con
  # 2>&1 finirebbe nella stessa stringa da cui si estrae il codice HTTP, e
  # basterebbe un messaggio a piu' righe per far leggere il codice sbagliato.
  if [ -n "$corpo" ]; then
    risposta=$(curl -sS -k --max-time 30 -u "$ES_USER:$ES_PASSWORD" \
      -X "$metodo" "$url" \
      -H 'Content-Type: application/json' \
      -d "$corpo" \
      -w $'\n%{http_code}' 2>"$TEMP/curl")
  else
    risposta=$(curl -sS -k --max-time 30 -u "$ES_USER:$ES_PASSWORD" \
      -X "$metodo" "$url" \
      -w $'\n%{http_code}' 2>"$TEMP/curl")
  fi

  CODICE=${risposta##*$'\n'}
  CORPO=${risposta%$'\n'*}

  # Connessione mai stabilita: %{http_code} vale 000 e il motivo lo sa solo
  # curl.
  case "$CODICE" in
    ''|*[!0-9]*) CODICE="000" ;;
  esac
  if [ "$CODICE" = "000" ]; then
    CORPO=$(tr '\n' ' ' < "$TEMP/curl")
  fi
}

# --- La policy ILM deve gia' esistere ---------------------------------------

chiama GET "$BASE/_ilm/policy/$LIFECYCLE"
case "$CODICE" in
  200) ;;
  401|403)
    echo "ERRORE: Elasticsearch ha rifiutato l'utente \"$ES_USER\" (HTTP $CODICE)." >&2
    echo "        Se la password contiene * \$ ? [ o spazi, mettila fra apici singoli." >&2
    exit 1
    ;;
  404)
    echo "ERRORE: la policy ILM \"$LIFECYCLE\" non esiste su $BASE." >&2
    echo "        I template la nominerebbero lo stesso e Elasticsearch li" >&2
    echo "        accetterebbe senza dire niente, ma il rollover non avverrebbe" >&2
    echo "        mai: l'indice -000001 crescerebbe finche' non finisce il disco." >&2
    echo "        La crea il ruolo loggingElastic (playbooks/70-logging.yml)." >&2
    exit 1
    ;;
  *)
    echo "ERRORE: $BASE non risponde come previsto (HTTP $CODICE)." >&2
    echo "        $CORPO" >&2
    exit 1
    ;;
esac

# --- Preparazione dell'elenco -----------------------------------------------
# sort -u e non sort: due righe identiche sono un nome di indice conteso da due
# namespace (succede togliendo i trattini: my-app e myapp -> myapp). Con il
# semplice sort passavano entrambe, la seconda PUT sovrascriveva la prima e non
# lo diceva nessuno.

tr -d '\r' < "$LISTA_INDICI" | awk 'NF' | sort > "$ORDINATA"
DOPPI=$(uniq -d < "$ORDINATA")
sort -u -o "$ORDINATA" "$ORDINATA"

if [ -n "$DOPPI" ]; then
  echo "ATTENZIONE: nomi ripetuti nell'elenco, elaborati una volta sola." >&2
  echo "            Sono namespace diversi finiti sullo stesso nome di indice:" >&2
  echo "            i loro log si mescoleranno li' dentro." >&2
  while IFS= read -r doppio; do
    echo "  $doppio" >&2
  done <<< "$DOPPI"
fi

if [ ! -s "$ORDINATA" ]; then
  echo "ERRORE: $LISTA_INDICI non contiene nessun nome." >&2
  exit 2
fi

: > "$FILE_RILANCIO"

CREATI=0
GIA_PRESENTI=0
RILANCIARE=0
FALLITI=0

# --- Ciclo ------------------------------------------------------------------

while IFS= read -r INDICE; do
  [ -n "$INDICE" ] || continue
  NEW_INDEX="$PREFISSO-$INDICE"

  echo ""
  echo "--------- $NEW_INDEX ---------"

  # 1. Index template.
  chiama PUT "$BASE/_index_template/$NEW_INDEX" "$(cat <<JSON
{
  "index_patterns": [ "$NEW_INDEX*" ],
  "priority": $PRIORITY,
  "template": {
    "settings": {
      "index": {
        "lifecycle": {
          "name": "$LIFECYCLE",
          "rollover_alias": "$NEW_INDEX"
        },
        "number_of_shards": "$SHARD",
        "number_of_replicas": "$REPLICA"
      }
    }
  }
}
JSON
)"

  if [ "$CODICE" != "200" ]; then
    if printf '%s' "$CORPO" | grep -q 'that have the same priority'; then
      # Il pattern "$NEW_INDEX*" si sovrappone a quello di un altro namespace
      # di cui questo nome contiene il prefisso (foo e foobar). Il piu'
      # specifico deve stare PIU' IN ALTO: va rilanciato a priorita' maggiore.
      echo "  template in conflitto di priorita': da rilanciare a $PRIORITA_SUCCESSIVA"
      echo "$INDICE" >> "$FILE_RILANCIO"
      RILANCIARE=$((RILANCIARE + 1))
    else
      echo "  ERRORE nella creazione del template (HTTP $CODICE): $CORPO" >&2
      FALLITI=$((FALLITI + 1))
    fi
    continue
  fi
  echo "  template creato (priorita' $PRIORITY)"

  # 2. L'alias esiste gia'? Allora l'insieme e' avviato e non si tocca: dove
  #    sta l'indice di scrittura lo decide ILM con il rollover.
  chiama GET "$BASE/_alias/$NEW_INDEX"
  if [ "$CODICE" = "200" ]; then
    echo "  alias gia' presente: indice e alias non toccati"
    GIA_PRESENTI=$((GIA_PRESENTI + 1))
    continue
  fi

  # 3. Indice di partenza CON l'alias dentro, in una richiesta sola. Facendone
  #    due c'e' una finestra in cui l'indice esiste senza alias: se il lancio si
  #    interrompe li', al giro dopo la PUT dell'indice fallisce con
  #    resource_already_exists_exception e l'alias non viene attaccato mai piu'.
  #
  #    Il nome DEVE finire con -000001: il rollover incrementa il numero in
  #    coda, e su un indice che non finisce con un numero non sa da dove
  #    ripartire.
  chiama PUT "$BASE/$NEW_INDEX-000001" \
    "{\"aliases\": {\"$NEW_INDEX\": {\"is_write_index\": true}}}"

  if [ "$CODICE" = "200" ]; then
    echo "  indice $NEW_INDEX-000001 creato, alias di scrittura agganciato"
    CREATI=$((CREATI + 1))
    continue
  fi

  # 4. L'indice c'era gia' ma senza alias: lancio precedente interrotto a meta'.
  if printf '%s' "$CORPO" | grep -q 'resource_already_exists_exception'; then
    chiama POST "$BASE/_aliases" \
      "{\"actions\": [ {\"add\": {\"index\": \"$NEW_INDEX-000001\", \"alias\": \"$NEW_INDEX\", \"is_write_index\": true}} ]}"
    if [ "$CODICE" = "200" ]; then
      echo "  indice gia' presente senza alias: alias di scrittura agganciato adesso"
      CREATI=$((CREATI + 1))
    else
      echo "  ERRORE nell'aggancio dell'alias (HTTP $CODICE): $CORPO" >&2
      FALLITI=$((FALLITI + 1))
    fi
    continue
  fi

  echo "  ERRORE nella creazione dell'indice (HTTP $CODICE): $CORPO" >&2
  FALLITI=$((FALLITI + 1))
done < "$ORDINATA"

# --- Riepilogo --------------------------------------------------------------

echo ""
echo "###################################################"
echo "creati o riparati adesso : $CREATI"
echo "gia' presenti            : $GIA_PRESENTI"
echo "da rilanciare            : $RILANCIARE"
echo "falliti                  : $FALLITI"

if [ "$RILANCIARE" -gt 0 ]; then
  echo ""
  echo "Questi namespace hanno un pattern che si sovrappone a quello di un altro"
  echo "e vanno rilanciati a priorita' $PRIORITA_SUCCESSIVA:"
  echo ""
  echo "  ./create-index.sh $HTTP $ELASTIC_HOST $ES_USER '<password>' $PREFISSO \\"
  echo "      $FILE_RILANCIO $PRIORITA_SUCCESSIVA $SHARD $REPLICA $LIFECYCLE_END"
  echo ""
  echo "(playbooks/70-logging.yml calcola queste priorita' da solo e non ha"
  echo " bisogno di nessun rilancio)"
else
  rm -f "$FILE_RILANCIO"
fi
echo "###################################################"

if [ "$FALLITI" -gt 0 ]; then
  exit 1
elif [ "$RILANCIARE" -gt 0 ]; then
  exit 3
fi
exit 0
