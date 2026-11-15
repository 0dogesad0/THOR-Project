/**
 * Thor x OpenCode — host embebido (corre com opencode/bun.exe, 100% local).
 *
 * Embebe o servidor OpenCode em memoria (sem portas HTTP) e liga o provider
 * "thor" ao llama-server local (127.0.0.1:8080). Uma guarda de fetch so
 * permite 127.0.0.1 — models.dev, updates, webfetch/websearch do agente,
 * MCP remoto: tudo bloqueado. Modo offline garantido em processo.
 *
 * Protocolo stdio — uma mensagem JSON por linha:
 *   <- {"cmd":"prompt","sid":"...","text":"...","directory":"C:\\..."}
 *   <- {"cmd":"stop","sid":"..."}   {"cmd":"forget","sid":"..."}
 *   <- {"cmd":"ping"}               {"cmd":"quit"}
 *   -> {"ev":"ready"|"fatal"|"pong"|"accepted"|"delta"|"info"|"tool_start"|
 *       "tool_result"|"error"|"stopped"|"done","sid":"...","turn":N,...}
 */
import { OpenCode } from "@opencode/sdk"

const HERE = import.meta.dir

/* ---------------- saida ---------------- */
function send(o: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(o) + "\n")
}
function log(msg: string): void {
  const d = new Date()
  const ts =
    String(d.getHours()).padStart(2, "0") +
    ":" +
    String(d.getMinutes()).padStart(2, "0") +
    ":" +
    String(d.getSeconds()).padStart(2, "0") +
    "." +
    String(d.getMilliseconds()).padStart(3, "0")
  process.stderr.write("[" + ts + "] " + msg + "\n")
}

/* ---------------- guarda de rede (modo offline) ---------------- */
const realFetch = globalThis.fetch.bind(globalThis)

function isLocal(raw: string): boolean {
  try {
    const u = new URL(raw)
    return u.hostname === "127.0.0.1" || u.hostname === "localhost" || u.hostname === "::1"
  } catch {
    return false
  }
}

const guardFetch = async (input: any, init?: RequestInit): Promise<Response> => {
  const raw: string =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : String((input && input.url) || input)
  if (isLocal(raw)) return realFetch(input, init)
  log("[offline] bloqueado: " + raw)
  return new Response("offline: o Thor so permite ligacoes a 127.0.0.1", { status: 503 })
}

/* ---------------- criar o host ---------------- */
let oc: Awaited<ReturnType<typeof OpenCode.create>>
try {
  oc = await OpenCode.create({
    config: { file: HERE + "/opencode.json" },
    models: { fetch: false },
    fetch: guardFetch as any,
    log: {
      level: "info",
      emit: (e) => log("[opencode:" + e.level + "] " + e.message + (e.cause ? " — " + String(e.cause) : "")),
    },
  })
} catch (e: any) {
  send({ ev: "fatal", content: "falha ao criar o OpenCode: " + String((e && e.message) || e) })
  process.exit(1)
}

/* ---------------- permissoes: allow automatico (parceria sem UI) ------------
 * O host embebido nao tem interface para responder a pedidos "ask": sem isto,
 * qualquer ferramenta que precise de aprovacao fica a espera para sempre e o
 * turno morre em silencio. O hook corre depois das regras do opencode.json e
 * forca "allow" — o Thor e quem distribui, por isso quem aprova e o proprio
 * Thor (100% local, sem prompts de UI). */
try {
  await oc.plugin({
    id: "thor-auto-allow",
    setup: async (ctx) => {
      await ctx.permission.hook("evaluate", (input) => {
        input.effect = "allow"
      })
    },
  })
  log("plugin thor-auto-allow ativo (permissoes: allow)")
} catch (e) {
  log("plugin thor-auto-allow falhou: " + String(e))
}

/* ---------------- estado das sessoes ---------------- */
type Entry = { ocID: string; dir: string; turn: number }
type ToolState = { sid: string; name?: string; input?: any; started: boolean }

const bySid = new Map<string, Entry>() // sid THOR -> sessao OpenCode (raiz)
const sidByOc = new Map<string, string>() // sessao OpenCode (raiz + subagentes) -> sid THOR
const tools = new Map<string, ToolState>() // id da chamada -> estado

function emit(ocSID: string, o: Record<string, unknown>): void {
  const sid = sidByOc.get(ocSID)
  if (!sid) return
  const e = bySid.get(sid)
  if (!e) return
  send({ ...o, sid, turn: e.turn })
}

function isRoot(ocSID: string): boolean {
  const sid = sidByOc.get(ocSID)
  return !!sid && bySid.get(sid)?.ocID === ocSID
}

function turnOf(sid: string | undefined): number {
  return sid ? bySid.get(sid)?.turn ?? 0 : 0
}

function maybeToolStart(id: string, st: ToolState): void {
  if (st.started || !st.name || st.input === undefined || st.input === null) return
  st.started = true
  send({
    ev: "tool_start",
    sid: st.sid,
    turn: turnOf(st.sid),
    name: st.name,
    args: JSON.stringify(st.input),
  })
}

function toolResult(d: any, failed: boolean): void {
  const st = tools.get(d.id)
  let text = ""
  if (failed) {
    const err = d.error
    text =
      "[erro] " +
      (typeof err === "string" ? err : (err && (err.message || err.type)) || JSON.stringify(err))
  } else if (Array.isArray(d.content)) {
    text = d.content
      .map((c: any) =>
        c && c.type === "text"
          ? c.text
          : c && c.type === "file"
            ? "[ficheiro " + (c.name || c.uri || "") + "]"
            : "",
      )
      .filter(Boolean)
      .join("\n")
  }
  if (!text) text = failed ? "[erro] falha desconhecida" : "(sem output)"
  if (text.length > 4000) text = text.slice(0, 4000) + "\n… [truncado]"

  const sid = (st && st.sid) || sidByOc.get(d.sessionID)
  if (!sid) return
  const name = (st && st.name) || "tool"
  if (st && !st.started) {
    // chegou o resultado antes do arranque (ordem inversa) — emite o cartao primeiro
    st.started = true
    send({
      ev: "tool_start",
      sid,
      turn: turnOf(sid),
      name,
      args: st.input ? JSON.stringify(st.input) : "{}",
    })
  }
  send({ ev: "tool_result", sid, turn: turnOf(sid), name, result: text })
  tools.delete(d.id)
}

/* ---------------- eventos do OpenCode -> eventos Thor ---------------- */
function handle(ev: any): void {
  const t: string = ev && ev.type
  const d: any = (ev && ev.data) || {}
  switch (t) {
    case "session.created": {
      // subagente: herda o sid Thor do pai para os eventos aparecerem na conversa
      if (d.parentID) {
        const psid = sidByOc.get(d.parentID)
        if (psid) {
          sidByOc.set(d.sessionID, psid)
          log("subagente " + d.sessionID + " associado a " + psid)
        }
      }
      break
    }
    case "session.text.delta":
      emit(d.sessionID, { ev: "delta", content: d.delta })
      break
    case "session.tool.input.started": {
      const sid = sidByOc.get(d.sessionID)
      if (!sid) break
      const st = tools.get(d.id) || { sid, started: false }
      st.name = d.name
      tools.set(d.id, st)
      maybeToolStart(d.id, st)
      break
    }
    case "session.tool.called": {
      const sid = sidByOc.get(d.sessionID)
      if (!sid) break
      const st = tools.get(d.id) || { sid, started: false }
      st.input = d.input
      tools.set(d.id, st)
      maybeToolStart(d.id, st)
      break
    }
    case "session.tool.success":
      toolResult(d, false)
      break
    case "session.tool.failed":
      toolResult(d, true)
      break
    case "session.execution.started":
      emit(d.sessionID, { ev: "info", content: "OpenCode: a executar…" })
      break
    case "session.execution.failed":
      emit(d.sessionID, {
        ev: "error",
        content:
          "[erro] " +
          (d.error && d.error.message ? d.error.message : JSON.stringify(d.error || "falha")),
      })
      if (isRoot(d.sessionID)) send({ ev: "done", sid: sidByOc.get(d.sessionID), turn: turnOf(sidByOc.get(d.sessionID) || undefined) })
      break
    case "session.execution.interrupted":
      if (isRoot(d.sessionID)) {
        emit(d.sessionID, { ev: "stopped", content: "⏹ interrompido pelo utilizador." })
        emit(d.sessionID, { ev: "done" })
      }
      break
    case "session.execution.succeeded":
      // NESTA versao do OpenCode o fim de turno vem aqui (session.idle NUNCA
      // chega): sem este caso, o turno acaba bem mas o bridge fica a espera
      // de "done" para sempre (watchdog). Apenas a sessao raiz fecha o turno.
      if (isRoot(d.sessionID)) emit(d.sessionID, { ev: "done" })
      break
    case "session.idle":
      // fallback: algumas versoes emitem idle; qualquer dos dois fecha o turno
      if (isRoot(d.sessionID)) emit(d.sessionID, { ev: "done" })
      break
    case "permission.asked":
      // nao deveria chegar com o plugin thor-auto-allow; se chegar, regista e avisa
      log("permission.asked " + d.action + " (sem resposta — o plugin devia ter dado allow)")
      emit(d.sessionID, {
        ev: "info",
        content: "permissao pedida (" + d.action + ") — allow automatico em atraso",
      })
      break
    default:
      // registo de diagnóstico: qualquer evento nao mapeado aparece no log
      // (silencio total = turno preso; ver .thor/opencode.log)
      if (t && t !== "session.text.delta") log("ev " + t)
      break
  }
}

async function pump(): Promise<void> {
  try {
    for await (const ev of oc.events.subscribe()) {
      try {
        handle(ev)
      } catch (e) {
        log("handle " + (ev as any)?.type + ": " + String(e))
      }
    }
    log("eventos fechados")
  } catch (e) {
    log("pump: " + String(e))
  }
}
pump()
send({ ev: "ready" })

/* ---------------- comandos do Thor ---------------- */
async function onCmd(c: any): Promise<void> {
  switch (c && c.cmd) {
    case "ping":
      send({ ev: "pong" })
      break

    case "prompt": {
      const sid = String(c.sid)
      const dir = String(c.directory || process.cwd())
      const text = String(c.text || "")
      log("prompt pedido: sid=" + sid + " dir=" + dir)
      let e = bySid.get(sid)
      if (!e) {
        const s = await oc.sessions.create({
          location: { directory: dir },
          model: { providerID: "thor", id: "model" },
        })
        e = { ocID: s.id, dir, turn: 0 }
        bySid.set(sid, e)
        sidByOc.set(s.id, sid)
        log("sessao criada " + s.id + " em " + dir)
      } else if (e.dir !== dir) {
        try {
          await oc.sessions.move({ sessionID: e.ocID, directory: dir })
          e.dir = dir
          log("sessao movida para " + dir)
        } catch (err) {
          log("session.move falhou: " + String(err))
        }
      }
      e.turn += 1
      send({ ev: "accepted", sid, turn: e.turn })
      // nao bloqueia o loop de stdin (o botao Parar tem de responder)
      oc.sessions.prompt({ sessionID: e.ocID, text }).catch((err: any) => {
        const turn = e ? e.turn : 0
        log("prompt() rejeitado: " + String((err && err.stack) || err))
        send({ ev: "error", sid, turn, content: "prompt falhou: " + String((err && err.message) || err) })
        send({ ev: "done", sid, turn })
      })
      break
    }

    case "stop": {
      const e = bySid.get(String(c.sid))
      if (e) {
        try {
          await oc.sessions.interrupt({ sessionID: e.ocID })
          log("interrupt enviado")
        } catch (err) {
          log("interrupt falhou: " + String(err))
        }
      }
      break
    }

    case "forget": {
      const sid = String(c.sid)
      const e = bySid.get(sid)
      if (e) {
        bySid.delete(sid)
        sidByOc.delete(e.ocID)
        for (const [tid, st] of tools) if (st.sid === sid) tools.delete(tid)
        try {
          await oc.sessions.remove({ sessionID: e.ocID })
        } catch (err) {
          log("session.remove falhou: " + String(err))
        }
        log("sessao esquecida: " + sid)
      }
      break
    }

    case "quit":
      try {
        await oc.close()
      } catch (err) {
        log("close: " + String(err))
      }
      process.exit(0)
      break

    default:
      log("cmd desconhecido: " + JSON.stringify(c).slice(0, 200))
  }
}

/* ---------------- loop de stdin (comandos serializados) ---------------- */
let chain: Promise<void> = Promise.resolve()
function onLine(line: string): void {
  let cmd: any
  try {
    cmd = JSON.parse(line)
  } catch {
    log("cmd invalido (" + line.length + " ch): " + JSON.stringify(line.slice(0, 300)))
    return
  }
  chain = chain.then(() => onCmd(cmd)).catch((e) => log("cmd erro: " + String((e && e.stack) || e)))
}

let buf = ""
process.stdin.on("data", (chunk: any) => {
  buf += Buffer.from(chunk).toString("utf-8")
  let i: number
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim()
    buf = buf.slice(i + 1)
    if (line) onLine(line)
  }
})
process.stdin.on("end", () => {
  log("stdin fechado — a encerrar")
  chain
    .then(() => oc.close())
    .catch(() => {})
    .finally(() => process.exit(0))
})
process.stdin.on("close", () => log("stdin close"))
process.stdin.on("error", (e: any) => log("stdin erro: " + String(e)))
