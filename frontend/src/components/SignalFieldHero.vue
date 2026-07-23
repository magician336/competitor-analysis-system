<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = withDefaults(defineProps<{ progress?: number }>(), { progress: 0 });
const canvas = ref<HTMLCanvasElement | null>(null);
let frame = 0;
let resizeObserver: ResizeObserver | null = null;
let visibilityObserver: IntersectionObserver | null = null;
let visible = true;
let reducedMotion = false;
let animationStartedAt = 0;
let scanStartedAt = -1;
const pointer = { x: 0, y: 0, canvasX: 0, canvasY: 0, inside: false };

type OrbitAxis = "horizontal" | "vertical";

interface OrbitSpec {
  scale: number;
  flatten: number;
  rotation: number;
  color: string;
  alpha: number;
  width: number;
  dash: number[];
  speed: number;
  axis: OrbitAxis;
  offset: number;
  arc?: [number, number];
}

const orbits: OrbitSpec[] = [
  { scale: .58, flatten: .77, rotation: -.3, color: "#2563eb", alpha: .52, width: 1.25, dash: [], speed: .000052, axis: "horizontal", offset: -2 },
  { scale: .77, flatten: .58, rotation: .16, color: "#7765e8", alpha: .42, width: 1, dash: [3, 9], speed: -.000037, axis: "vertical", offset: -2 },
  { scale: .96, flatten: .72, rotation: -.08, color: "#2db8a3", alpha: .38, width: 1.15, dash: [], speed: .000031, axis: "horizontal", offset: 0 },
  { scale: 1.14, flatten: .61, rotation: .27, color: "#2563eb", alpha: .28, width: .8, dash: [14, 17], speed: -.000024, axis: "vertical", offset: 0 },
  { scale: 1.31, flatten: .79, rotation: -.2, color: "#7765e8", alpha: .22, width: .75, dash: [2, 11], speed: .000021, axis: "horizontal", offset: 2 },
  { scale: 1.48, flatten: .66, rotation: .08, color: "#2db8a3", alpha: .18, width: .7, dash: [8, 20], speed: -.000018, axis: "vertical", offset: 2 },
  { scale: 1.65, flatten: .82, rotation: -.34, color: "#2563eb", alpha: .3, width: 1, dash: [], speed: .000016, axis: "horizontal", offset: 4, arc: [-.08, .43] },
  { scale: 1.78, flatten: .7, rotation: .32, color: "#7765e8", alpha: .24, width: .9, dash: [], speed: -.000014, axis: "vertical", offset: 4, arc: [.48, .92] },
  { scale: 1.91, flatten: .55, rotation: -.12, color: "#2db8a3", alpha: .16, width: .68, dash: [4, 15], speed: .000012, axis: "horizontal", offset: -4, arc: [.1, .68] },
  { scale: 2.08, flatten: .76, rotation: .22, color: "#7765e8", alpha: .14, width: .64, dash: [1, 13], speed: -.000011, axis: "vertical", offset: -4, arc: [.34, .9] },
];

const nodeColors = ["#2563eb", "#7765e8", "#2db8a3", "#4f8df5", "#9a72e8", "#45b7a7"];

function clamp(value: number, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

function smoothstep(start: number, end: number, value: number) {
  const normalized = clamp((value - start) / (end - start));
  return normalized * normalized * (3 - 2 * normalized);
}

function mix(from: number, to: number, progress: number) {
  return from + (to - from) * progress;
}

function sizeCanvas() {
  const element = canvas.value;
  if (!element) return;
  const rect = element.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  element.width = Math.max(1, Math.floor(rect.width * ratio));
  element.height = Math.max(1, Math.floor(rect.height * ratio));
  element.getContext("2d")?.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw(performance.now());
}

function draw(now: number) {
  const element = canvas.value;
  const context = element?.getContext("2d");
  if (!element || !context) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = element.width / ratio;
  const height = element.height / ratio;
  const progress = reducedMotion ? 0.16 : clamp(props.progress);
  const introReveal = reducedMotion ? 1 : smoothstep(0, 900, now - animationStartedAt);
  const reveal = Math.max(introReveal, smoothstep(0, 0.18, progress));
  const focus = smoothstep(0.46, 0.72, progress);
  const gridMorph = smoothstep(0.72, 0.9, progress);
  const settle = smoothstep(0.9, 1, progress);
  const centerX = width / 2;
  const centerY = height * 0.43;
  const initialRadius = Math.max(Math.min(width, height) * 0.33, width * 0.31);
  const baseRadius = initialRadius * (1 - focus * 0.13);
  const breathing = reducedMotion ? 0 : Math.sin(now * 0.00042) * 0.009;
  const compact = width < 680;
  const activeOrbits = compact ? orbits.slice(0, 7) : orbits;
  const spacing = compact ? 32 : 48;
  const lensRadius = Math.min(width, height) * mix(0.22, 0.48, focus) * (1 - settle * 0.34);

  context.clearRect(0, 0, width, height);

  const glowRadius = Math.max(width, height) * .72;
  const glow = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, glowRadius);
  glow.addColorStop(0, `rgba(119, 101, 232, ${0.095 * reveal * (1 - settle)})`);
  glow.addColorStop(.48, `rgba(68, 133, 232, ${0.052 * reveal * (1 - settle)})`);
  glow.addColorStop(1, "rgba(244, 241, 234, 0)");
  context.fillStyle = glow;
  context.fillRect(0, 0, width, height);

  function pointFor(orbit: OrbitSpec, position: number, snapToGrid = false) {
    const arcStart = orbit.arc?.[0] ?? 0;
    const arcEnd = orbit.arc?.[1] ?? 1;
    const normalized = mix(arcStart, arcEnd, position);
    const angle = normalized * Math.PI * 2;
    const radius = baseRadius * orbit.scale * (1 + breathing * orbit.scale);
    const localX = Math.cos(angle) * radius;
    const localY = Math.sin(angle) * radius * orbit.flatten;
    const cosine = Math.cos(orbit.rotation);
    const sine = Math.sin(orbit.rotation);
    let orbitX = centerX + localX * cosine - localY * sine;
    let orbitY = centerY + localX * sine + localY * cosine;

    const distance = Math.hypot(orbitX - centerX, orbitY - centerY);
    const refraction = clamp(1 - distance / Math.max(lensRadius, 1)) * 7 * (1 - gridMorph);
    orbitX += ((orbitX - centerX) / Math.max(distance, 1)) * refraction;
    orbitY += ((orbitY - centerY) / Math.max(distance, 1)) * refraction;

    if (pointer.inside && !reducedMotion) {
      const pointerDistance = Math.hypot(orbitX - pointer.canvasX, orbitY - pointer.canvasY);
      const pointerInfluence = smoothstep(190, 0, pointerDistance) * (1 - gridMorph);
      orbitX += ((orbitX - pointer.canvasX) / Math.max(pointerDistance, 1)) * pointerInfluence * 7;
      orbitY += ((orbitY - pointer.canvasY) / Math.max(pointerDistance, 1)) * pointerInfluence * 7;
    }

    const lineX = centerX + Math.cos(angle) * width * .62;
    const lineY = centerY + Math.sin(angle) * height * .72;
    const targetX = orbit.axis === "horizontal"
      ? (snapToGrid ? centerX + Math.round((lineX - centerX) / spacing) * spacing : lineX)
      : centerX + orbit.offset * spacing;
    const targetY = orbit.axis === "horizontal"
      ? centerY + orbit.offset * spacing
      : (snapToGrid ? centerY + Math.round((lineY - centerY) / spacing) * spacing : lineY);
    return {
      x: mix(orbitX, targetX, gridMorph),
      y: mix(orbitY, targetY, gridMorph),
    };
  }

  activeOrbits.forEach((orbit) => {
    context.beginPath();
    for (let sample = 0; sample <= 96; sample += 1) {
      const point = pointFor(orbit, sample / 96);
      if (sample === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    }
    context.setLineDash(orbit.dash.map((value) => mix(value, 0, gridMorph)));
    context.lineWidth = mix(orbit.width, .72, gridMorph);
    context.strokeStyle = orbit.color;
    context.globalAlpha = orbit.alpha * reveal * mix(1, .5, settle);
    context.stroke();
  });
  context.setLineDash([]);
  context.globalAlpha = 1;

  const nodeCount = compact ? 10 : 16;
  for (let index = 0; index < nodeCount; index += 1) {
    const orbit = activeOrbits[index % activeOrbits.length];
    const motion = reducedMotion ? 0 : now * orbit.speed;
    const position = (index / nodeCount + motion + 1) % 1;
    const point = pointFor(orbit, position, true);
    const color = nodeColors[index % nodeColors.length];
    const pointerDistance = pointer.inside ? Math.hypot(point.x - pointer.canvasX, point.y - pointer.canvasY) : 999;
    const pointerProximity = smoothstep(170, 0, pointerDistance) * (1 - gridMorph);
    const nodeSize = (2.2 + (index % 4) * .72) * (1 + pointerProximity * .7);
    context.beginPath();
    context.arc(point.x, point.y, nodeSize, 0, Math.PI * 2);
    context.fillStyle = color;
    context.globalAlpha = reveal * mix(.82, .58, gridMorph) * (index % 3 === 0 ? .75 : 1) * (1 + pointerProximity * .22);
    context.shadowBlur = (12 + pointerProximity * 15) * (1 - gridMorph);
    context.shadowColor = color;
    context.fill();
  }
  context.shadowBlur = 0;
  context.globalAlpha = 1;

  const highlightX = centerX + pointer.x * lensRadius * .13 * (1 - gridMorph);
  const highlightY = centerY + pointer.y * lensRadius * .1 * (1 - gridMorph);
  const lens = context.createRadialGradient(highlightX, highlightY, lensRadius * .06, centerX, centerY, lensRadius);
  lens.addColorStop(0, `rgba(251, 250, 246, ${.26 * reveal * (1 - settle)})`);
  lens.addColorStop(.56, `rgba(112, 137, 232, ${.07 * reveal * (1 - settle)})`);
  lens.addColorStop(.84, `rgba(119, 101, 232, ${.035 * reveal * (1 - settle)})`);
  lens.addColorStop(1, "rgba(119, 101, 232, 0)");
  context.beginPath();
  context.arc(centerX, centerY, lensRadius, 0, Math.PI * 2);
  context.fillStyle = lens;
  context.fill();
  context.beginPath();
  context.ellipse(centerX, centerY, lensRadius, lensRadius * .96, -.08, 0, Math.PI * 2);
  context.lineWidth = .8;
  context.strokeStyle = `rgba(37, 99, 235, ${.2 * reveal * (1 - settle)})`;
  context.stroke();

  const coreAlpha = reveal * (1 - settle) * mix(1, .72, focus);
  const coreRadius = Math.max(34, lensRadius * .34);
  const coreRotation = reducedMotion ? 0 : now * .00009;
  const irisColors = ["#2563eb", "#7765e8", "#2db8a3", "#7765e8"];
  for (let segment = 0; segment < 4; segment += 1) {
    const start = coreRotation + segment * Math.PI / 2 + .18;
    context.beginPath();
    context.arc(centerX, centerY, coreRadius * (segment % 2 ? .82 : 1), start, start + Math.PI * .34);
    context.lineWidth = segment % 2 ? 1 : 1.35;
    context.strokeStyle = irisColors[segment];
    context.globalAlpha = coreAlpha * (segment % 2 ? .42 : .66);
    context.stroke();
  }

  context.setLineDash([2, 7]);
  context.beginPath();
  context.arc(centerX, centerY, coreRadius * .62, 0, Math.PI * 2);
  context.lineWidth = .8;
  context.strokeStyle = "#496fca";
  context.globalAlpha = coreAlpha * .34;
  context.stroke();
  context.setLineDash([]);

  for (let tickIndex = 0; tickIndex < 8; tickIndex += 1) {
    const angle = tickIndex * Math.PI / 4 + coreRotation * .3;
    const inner = coreRadius * 1.08;
    const outer = inner + (tickIndex % 2 ? 4 : 8);
    context.beginPath();
    context.moveTo(centerX + Math.cos(angle) * inner, centerY + Math.sin(angle) * inner);
    context.lineTo(centerX + Math.cos(angle) * outer, centerY + Math.sin(angle) * outer);
    context.strokeStyle = tickIndex % 3 === 0 ? "#2db8a3" : "#2563eb";
    context.globalAlpha = coreAlpha * .42;
    context.lineWidth = .8;
    context.stroke();
  }

  const crosshairGap = coreRadius * .18;
  const crosshairLength = coreRadius * .42;
  context.strokeStyle = "#315fb8";
  context.lineWidth = .72;
  context.globalAlpha = coreAlpha * .28;
  context.beginPath();
  context.moveTo(centerX - crosshairLength, centerY);
  context.lineTo(centerX - crosshairGap, centerY);
  context.moveTo(centerX + crosshairGap, centerY);
  context.lineTo(centerX + crosshairLength, centerY);
  context.moveTo(centerX, centerY - crosshairLength);
  context.lineTo(centerX, centerY - crosshairGap);
  context.moveTo(centerX, centerY + crosshairGap);
  context.lineTo(centerX, centerY + crosshairLength);
  context.stroke();

  if (scanStartedAt >= 0) {
    const scanProgress = clamp((now - scanStartedAt) / 1250);
    const scanRadius = mix(coreRadius * .34, lensRadius * 1.55, scanProgress);
    context.beginPath();
    context.arc(centerX, centerY, scanRadius, 0, Math.PI * 2);
    context.strokeStyle = scanProgress < .55 ? "#2563eb" : "#2db8a3";
    context.lineWidth = mix(1.8, .5, scanProgress);
    context.globalAlpha = (1 - scanProgress) * .55 * coreAlpha;
    context.stroke();
    if (scanProgress >= 1) scanStartedAt = -1;
  }
  context.globalAlpha = 1;
}

function tick(now: number) {
  draw(now);
  if (visible && !reducedMotion) frame = window.requestAnimationFrame(tick);
}

function start() {
  window.cancelAnimationFrame(frame);
  if (visible && !reducedMotion) frame = window.requestAnimationFrame(tick);
  else draw(performance.now());
}

function onPointerMove(event: PointerEvent) {
  const rect = canvas.value?.getBoundingClientRect();
  if (!rect) return;
  pointer.canvasX = event.clientX - rect.left;
  pointer.canvasY = event.clientY - rect.top;
  pointer.inside = pointer.canvasX >= 0 && pointer.canvasX <= rect.width
    && pointer.canvasY >= 0 && pointer.canvasY <= rect.height;
  pointer.x = clamp(pointer.canvasX / Math.max(rect.width, 1), 0, 1) - .5;
  pointer.y = clamp(pointer.canvasY / Math.max(rect.height, 1), 0, 1) - .5;
}

function onPointerDown(event: PointerEvent) {
  if (reducedMotion) return;
  onPointerMove(event);
  const rect = canvas.value?.getBoundingClientRect();
  if (!rect || !pointer.inside) return;
  const progress = clamp(props.progress);
  const focus = smoothstep(.46, .72, progress);
  const settle = smoothstep(.9, 1, progress);
  const lensRadius = Math.min(rect.width, rect.height) * mix(.22, .48, focus) * (1 - settle * .34);
  const distance = Math.hypot(pointer.canvasX - rect.width / 2, pointer.canvasY - rect.height * .43);
  if (distance <= lensRadius) scanStartedAt = performance.now();
}

function onVisibility() {
  visible = !document.hidden;
  start();
}

watch(() => props.progress, () => draw(performance.now()));

onMounted(() => {
  reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  animationStartedAt = performance.now();
  resizeObserver = new ResizeObserver(sizeCanvas);
  if (canvas.value) {
    resizeObserver.observe(canvas.value);
    if ("IntersectionObserver" in window) {
      visibilityObserver = new IntersectionObserver(([entry]) => {
        visible = Boolean(entry?.isIntersecting) && !document.hidden;
        start();
      }, { threshold: 0.02 });
      visibilityObserver.observe(canvas.value);
    }
  }
  window.addEventListener("pointermove", onPointerMove, { passive: true });
  window.addEventListener("pointerdown", onPointerDown, { passive: true });
  document.addEventListener("visibilitychange", onVisibility);
  sizeCanvas();
  start();
});

onBeforeUnmount(() => {
  window.cancelAnimationFrame(frame);
  resizeObserver?.disconnect();
  visibilityObserver?.disconnect();
  window.removeEventListener("pointermove", onPointerMove);
  window.removeEventListener("pointerdown", onPointerDown);
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<template>
  <canvas
    ref="canvas"
    class="signal-field"
    data-visual="signal-observatory"
    data-orbit-count="10"
    data-mobile-orbit-count="7"
    data-node-count="16"
    data-mobile-node-count="10"
    data-interaction="lens-refraction scan-ripple"
    aria-hidden="true"
  />
</template>
