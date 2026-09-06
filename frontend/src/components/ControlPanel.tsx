"use client";

import { useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { referenceUrl } from "@/lib/api";

export function ControlPanel({ studio }: { studio: any }) {
  const [addHex, setAddHex] = useState("#8B5E3C");
  const [palName, setPalName] = useState("");
  const [isGenRef, setIsGenRef] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const s = studio.settings;

  const promptRef = useRef<HTMLTextAreaElement>(null);
  const typeRef = useRef<HTMLSelectElement>(null);
  const sizeRef = useRef<HTMLSelectElement>(null);
  const modelRef = useRef<HTMLSelectElement>(null);
  const sysRef = useRef<HTMLTextAreaElement>(null);
  const refModelRef = useRef<HTMLSelectElement>(null);
  const modeRef = useRef<HTMLSelectElement>(null);
  const refineRef = useRef<HTMLInputElement>(null);
  const seedModeRef = useRef<HTMLSelectElement>(null);
  const workflowRef = useRef<HTMLSelectElement>(null);

  const PHASES = ["silhouette", "base_colors", "shading", "detail", "cleanup"];

  const handleGenerate = async () => {
    const prompt = promptRef.current?.value?.trim();
    if (!prompt || !studio.currentPalette) return;
    await studio.generate({
      prompt,
      size: parseInt(sizeRef.current?.value || "16"),
      model: modelRef.current?.value || s.default_model,
      spriteType: typeRef.current?.value || "block",
      systemPrompt: sysRef.current?.value,
      mode: modeRef.current?.value || "auto",
      refine: refineRef.current?.checked || false,
      seedMode: seedModeRef.current?.value || "soft",
      workflow: workflowRef.current?.value || undefined,
    });
  };

  const handleGenRef = async () => {
    const prompt = promptRef.current?.value?.trim();
    if (!prompt) return;
    setIsGenRef(true);
    try {
      await studio.generateReference(
        prompt,
        refModelRef.current?.value || s.default_image_model,
        typeRef.current?.value || "block"
      );
    } catch (e: any) {
      alert(e.message);
    }
    setIsGenRef(false);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await studio.uploadReference(file);
    } catch (err: any) {
      alert(err.message);
    }
    e.target.value = "";
  };

  const handleRevise = async () => {
    const fb = window.prompt("What should change?");
    if (!fb) return;
    try {
      await studio.reviseReference(
        promptRef.current?.value || "",
        fb,
        refModelRef.current?.value || s.default_image_model,
        typeRef.current?.value || "block"
      );
    } catch (e: any) {
      alert(e.message);
    }
  };

  return (
    <div className="w-[280px] shrink-0 flex flex-col h-screen overflow-hidden" style={{ borderRight: "1px solid var(--border)" }}>

      {/* Header */}
      <div className="px-3.5 py-3 flex items-center gap-2" style={{ borderBottom: "1px solid var(--border)" }}>
        <div style={{ width: 6, height: 6, background: "var(--accent)", transform: "rotate(45deg)" }} />
        <span style={{ fontSize: "11px", fontWeight: 600, letterSpacing: "0.05em" }}>TEXEL STUDIO</span>
      </div>

      <div className="flex-1 overflow-y-auto">

        {/* ── Palette ── */}
        <div className="panel-section">
          <div className="label">Palette</div>
          <select
            value={studio.currentPalette?.id || ""}
            onChange={(e) => studio.selectPalette(parseInt(e.target.value))}
          >
            {studio.palettes.map((p: any) => (
              <option key={p.id} value={p.id}>{p.name} ({p.colors.length})</option>
            ))}
          </select>

          {/* Swatches */}
          <div className="flex flex-wrap gap-[2px] mt-2.5">
            {studio.currentPalette?.colors.map((c: string, i: number) => (
              <motion.button
                key={i}
                whileHover={{ scale: 1.15 }}
                whileTap={{ scale: 0.95 }}
                className="relative"
                style={{
                  width: 22,
                  height: 22,
                  background: c,
                  border: "none",
                  cursor: "pointer",
                  outline: i === studio.selectedColorIdx ? "2px solid var(--accent)" : "1px solid var(--border)",
                  outlineOffset: i === studio.selectedColorIdx ? "1px" : "0",
                  zIndex: i === studio.selectedColorIdx ? 2 : 1,
                }}
                onClick={() => studio.setSelectedColorIdx(i)}
                title={`${i}: ${c}`}
              />
            ))}
          </div>

          {/* Add color */}
          <div className="flex gap-1 mt-2">
            <input
              type="color"
              value={addHex}
              onChange={(e) => setAddHex(e.target.value)}
              style={{
                width: 28,
                height: 28,
                padding: 0,
                border: "1px solid var(--border)",
                background: "transparent",
                cursor: "pointer",
                borderRadius: 0,
              }}
            />
            <input
              type="text"
              value={addHex}
              onChange={(e) => setAddHex(e.target.value)}
              maxLength={7}
              style={{ fontSize: "10px", flex: 1 }}
            />
            <button className="btn" onClick={() => studio.addColor(addHex)}>+</button>
          </div>

          {/* Save palette as */}
          <div className="flex gap-1 mt-1.5">
            <input
              type="text"
              value={palName}
              onChange={(e) => setPalName(e.target.value)}
              placeholder="save palette as..."
              style={{ fontSize: "10px" }}
            />
            <button
              className="btn"
              onClick={() => {
                if (palName.trim()) {
                  studio.savePaletteAs(palName.trim());
                  setPalName("");
                }
              }}
            >
              save
            </button>
          </div>
        </div>

        {/* ── Prompt & Settings ── */}
        <div className="panel-section">
          <div className="label">Generate</div>
          <textarea
            ref={promptRef}
            placeholder="a dirt block with embedded pebbles and thin root fragments..."
            rows={4}
          />

          <div className="grid grid-cols-2 gap-1.5 mt-2.5">
            <div>
              <div style={{ fontSize: "9px", color: "var(--text-faint)", marginBottom: 3 }}>type</div>
              <select ref={typeRef} defaultValue="block">
                {Object.entries(s.sprite_types || {}).map(([k, v]: [string, any]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
            </div>
            <div>
              <div style={{ fontSize: "9px", color: "var(--text-faint)", marginBottom: 3 }}>size</div>
              <select ref={sizeRef} defaultValue="16">
                {[8, 16, 32, 64, 128].map((n) => (
                  <option key={n} value={n}>{n}x{n}</option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ fontSize: "9px", color: "var(--text-faint)", marginBottom: 3, marginTop: 8 }}>model</div>
          <select ref={modelRef} defaultValue={s.default_model}>
            {s.models.map((m: string) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>

          <details className="mt-2.5" style={{ color: "var(--text-dim)", fontSize: "10px" }}>
            <summary className="cursor-pointer select-none" style={{ transition: "color 0.15s" }}>
              system prompt
            </summary>
            <textarea
              ref={sysRef}
              defaultValue={s.system_prompt}
              rows={3}
              className="mt-1.5"
              style={{ fontSize: "10px", minHeight: 40 }}
            />
          </details>
        </div>

        {/* ── Reference Image ── */}
        <div className="panel-section">
          <div className="label">Reference</div>
          <div style={{ fontSize: "9px", color: "var(--text-faint)", marginBottom: 3 }}>concept model</div>
          <select ref={refModelRef} defaultValue={s.default_image_model}>
            {s.image_models.map((m: string) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>

          <div className="flex gap-1 mt-2">
            <button className="btn flex-1" onClick={handleGenRef} disabled={isGenRef}>
              {isGenRef ? "generating..." : "generate"}
            </button>
            <button className="btn flex-1" onClick={() => fileRef.current?.click()}>upload</button>
            <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={handleUpload} />
          </div>

          {/* Reference preview */}
          <AnimatePresence>
            {studio.referenceId && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-2 overflow-hidden"
              >
                <img
                  src={referenceUrl(studio.referenceId)}
                  alt="reference"
                  className="w-full"
                  style={{ border: "1px solid var(--border)", display: "block" }}
                />
                <div className="flex gap-1 mt-1.5">
                  <button
                    className={`btn flex-1 ${studio.refConfirmed ? "btn-primary" : ""}`}
                    onClick={studio.confirmReference}
                  >
                    {studio.refConfirmed ? "confirmed" : "confirm"}
                  </button>
                  <button className="btn flex-1" onClick={handleRevise}>revise</button>
                  <button className="btn btn-danger" onClick={studio.clearReference}>x</button>
                </div>
                <button
                  className="btn w-full mt-1.5"
                  style={{ fontSize: "10px" }}
                  onClick={studio.extractReferencePalette}
                  title="Extract colors from reference into active palette"
                >
                  copy palette from ref
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* ── Bottom: Generate Button ── */}
      <div className="p-3" style={{ borderTop: "1px solid var(--border)" }}>
        <div className="flex gap-1 items-center mb-1.5" style={{ fontSize: "10px" }}>
          <select ref={modeRef} defaultValue="auto" className="flex-1" title="Cómo se genera: auto elige image-first si hay referencia, agent si no">
            <option value="auto">mode: auto</option>
            <option value="image-first">image-first</option>
            <option value="agent">agent</option>
          </select>
          <label className="flex items-center gap-1 select-none" style={{ color: "var(--text-dim)" }} title="Solo image-first: pasada corta del LLM que arregla defectos sin redibujar">
            <input type="checkbox" ref={refineRef} /> refine
          </label>
          <select ref={seedModeRef} defaultValue="soft" className="flex-1" title="Solo agent: de dónde parte el agente y si la silueta de la referencia queda congelada">
            <option value="soft">seed: soft</option>
            <option value="locked">seed: locked</option>
            <option value="off">seed: off</option>
          </select>
          <select ref={workflowRef} defaultValue="" className="flex-1" title="Solo agent: phased trabaja en fases con control de calidad; freeform dibuja libre en un pase">
            <option value="">workflow: auto</option>
            <option value="phased">phased</option>
            <option value="freeform">freeform</option>
          </select>
        </div>

        {/* Ayuda de opciones */}
        <details className="mb-1.5" style={{ color: "var(--text-dim)", fontSize: "10px" }}>
          <summary className="cursor-pointer select-none">¿qué hace cada opción?</summary>
          <div style={{ marginTop: 6, lineHeight: 1.5 }}>
            <p style={{ marginBottom: 6, color: "var(--text-faint)" }}>
              Regla rápida: <b>foto/imagen de referencia → deja todo en auto</b>. Prompt de
              texto sin referencia y quieres control de estilo → <b>mode: agent</b>.
            </p>

            <p><b>mode</b> — cómo se construye el sprite</p>
            <ul style={{ paddingLeft: 14, marginBottom: 6 }}>
              <li><b>auto</b>: con referencia usa <i>image-first</i>; sin referencia usa <i>agent</i>.</li>
              <li><b>image-first</b>: redimensiona la imagen → la cuantiza a tu paleta → limpia
                píxeles sueltos y fondo. Rápido (~2 s), fiel y siempre igual. Sin referencia,
                genera antes una imagen conceptual con IA.
                <br /><i>Ej.:</i> subes una foto de una espada y quieres su versión pixel art.</li>
              <li><b>agent</b>: un LLM dibuja con herramientas (rectángulos, líneas, píxeles…),
                mirando su avance. Más lento, más control creativo.
                <br /><i>Ej.:</i> «bloque de piedra musgosa estilo Terraria» sin referencia.</li>
            </ul>

            <p><b>refine</b> — solo con image-first</p>
            <ul style={{ paddingLeft: 14, marginBottom: 6 }}>
              <li>Tras el render, el LLM hace una pasada corta que corrige <i>solo</i> defectos
                (píxeles sueltos, bordes rotos, algún color mal). No redibuja.
                Cuesta una llamada extra: úsalo si el resultado se ve «con ruido», déjalo
                apagado si ya está limpio.</li>
            </ul>

            <p><b>seed</b> — solo con mode: agent — punto de partida</p>
            <ul style={{ paddingLeft: 14, marginBottom: 6 }}>
              <li><b>soft</b> (por defecto): el agente arranca desde una conversión rápida de la
                referencia y la refina; puede reformar el contorno si hace falta.</li>
              <li><b>locked</b>: igual, pero la silueta (opaco/transparente) queda <i>congelada</i>;
                el agente solo cambia color y detalle dentro de la forma.
                <br /><i>Ej.:</i> el contorno de tu referencia ya es exacto y no quieres que lo toque.</li>
              <li><b>off</b>: lienzo en blanco, el agente construye todo desde cero. Úsalo sin
                referencia.</li>
            </ul>

            <p><b>workflow</b> — solo con mode: agent — cómo trabaja</p>
            <ul style={{ paddingLeft: 14, marginBottom: 6 }}>
              <li><b>auto</b>: <i>phased</i> si hay referencia, <i>freeform</i> si no.</li>
              <li><b>phased</b>: 5 fases con control de calidad — silueta → colores base →
                sombreado → detalle → limpieza. Tras la silueta la compara con la referencia y
                reintenta si no encaja. Resultados más legibles; más lento.</li>
              <li><b>freeform</b>: dibuja libre en un solo pase. Más rápido, menos predecible.</li>
            </ul>

            <p style={{ color: "var(--text-faint)" }}>
              Cada generación termina con una <b>nota de parecido 0–100</b> (IA que compara el
              sprite con tu petición). Si es baja te ofrece «seguir dibujando N pasos más».
              Las generaciones son deterministas por defecto (temperatura baja + semilla fija):
              repetir la misma petición da el mismo resultado.
            </p>
          </div>
        </details>

        {/* Phase tracker (spec 0005) */}
        {studio.currentPhase && (
          <div className="flex gap-1 mb-1.5" style={{ fontSize: "9px" }}>
            {PHASES.map((p) => {
              const active = studio.currentPhase?.startsWith(p);
              const done = PHASES.indexOf(p) < PHASES.findIndex((q) => studio.currentPhase?.startsWith(q));
              return (
                <span
                  key={p}
                  style={{
                    flex: 1, textAlign: "center", padding: "2px 0",
                    border: "1px solid var(--border)",
                    background: active ? "var(--accent)" : "transparent",
                    color: active ? "#000" : done ? "var(--text-dim)" : "var(--text-faint)",
                  }}
                >
                  {p.replace("_", " ")}
                </span>
              );
            })}
          </div>
        )}
        <div className="flex gap-1">
          <button
            className="btn btn-primary flex-1"
            onClick={handleGenerate}
            disabled={studio.isGenerating}
          >
            {studio.isGenerating ? "painting..." : "generate sprite"}
          </button>
          {studio.isGenerating && (
            <motion.button
              initial={{ opacity: 0, width: 0 }}
              animate={{ opacity: 1, width: "auto" }}
              className="btn"
              onClick={studio.skipAndFinalize}
            >
              skip
            </motion.button>
          )}
        </div>

        {/* Score gate decision (spec 0007) */}
        <AnimatePresence>
          {studio.pendingScore && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="mt-2 p-2"
              style={{ border: "1px solid var(--border)", fontSize: "10px", lineHeight: 1.4 }}
            >
              <div style={{ color: "var(--danger)" }}>
                score {studio.pendingScore.score}/100 — {studio.pendingScore.reason}
              </div>
              {studio.pendingScore.gaps?.length > 0 && (
                <ul style={{ margin: "4px 0 6px", paddingLeft: 14, color: "var(--text-dim)" }}>
                  {studio.pendingScore.gaps.map((g: string, i: number) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              )}
              <div className="flex gap-1">
                <button
                  className="btn btn-primary flex-1"
                  onClick={() => studio.continueDrawing(true, studio.pendingScore.extra_steps)}
                >
                  draw {studio.pendingScore.extra_steps} more steps
                </button>
                <button className="btn flex-1" onClick={() => studio.continueDrawing(false)}>
                  good enough
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Status */}
        <AnimatePresence>
          {studio.status.message && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              style={{
                fontSize: "10px",
                marginTop: 6,
                wordBreak: "break-word",
                lineHeight: 1.4,
                color:
                  studio.status.type === "generating" ? "var(--accent)" :
                  studio.status.type === "complete" ? "var(--success)" :
                  studio.status.type === "error" ? "var(--danger)" :
                  "var(--text-dim)",
              }}
            >
              {studio.status.message}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
