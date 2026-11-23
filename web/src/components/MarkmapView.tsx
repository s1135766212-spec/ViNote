import { useCallback, useEffect, useRef } from 'react';
import { Transformer } from 'markmap-lib';
import { Markmap } from 'markmap-view';
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';
import { fitMarkmapWhenSized } from './markmapViewport.js';

const transformer = new Transformer();

interface MarkmapViewProps {
  content: string;
  className?: string;
}

export default function MarkmapView({ content, className }: MarkmapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const mmRef = useRef<Markmap | null>(null);
  const fitFrameRef = useRef<number | null>(null);

  const scheduleFit = useCallback(() => {
    if (document.visibilityState === 'hidden') return;
    if (fitFrameRef.current !== null) cancelAnimationFrame(fitFrameRef.current);

    // Wait two frames so flex layout and Markmap's SVG nodes have both settled.
    fitFrameRef.current = requestAnimationFrame(() => {
      fitFrameRef.current = requestAnimationFrame(() => {
        fitFrameRef.current = null;
        fitMarkmapWhenSized(containerRef.current, () => mmRef.current?.fit());
      });
    });
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(scheduleFit)
      : null;
    observer?.observe(container);
    window.addEventListener('resize', scheduleFit);
    document.addEventListener('visibilitychange', scheduleFit);
    scheduleFit();

    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', scheduleFit);
      document.removeEventListener('visibilitychange', scheduleFit);
      if (fitFrameRef.current !== null) {
        cancelAnimationFrame(fitFrameRef.current);
        fitFrameRef.current = null;
      }
    };
  }, [scheduleFit]);

  useEffect(() => {
    if (!svgRef.current || !content) return;

    const { root } = transformer.transform(content);

    if (mmRef.current) {
      mmRef.current.setData(root);
      scheduleFit();
    } else {
      mmRef.current = Markmap.create(svgRef.current, {
        autoFit: false,
        duration: 500,
      }, root);
      scheduleFit();
    }
  }, [content, scheduleFit]);

  useEffect(() => {
    return () => {
      if (mmRef.current) {
        mmRef.current.destroy();
        mmRef.current = null;
      }
    };
  }, []);

  const handleZoomIn = () => mmRef.current?.rescale(1.25);
  const handleZoomOut = () => mmRef.current?.rescale(0.8);
  const handleFit = () => mmRef.current?.fit();

  return (
    <div
      ref={containerRef}
      className={`relative w-full min-h-[400px] ${className || 'h-[70vh] max-h-[800px]'}`}
    >
      <style>{`
        .markmap-wrap svg text,
        .markmap-wrap svg foreignObject,
        .markmap-wrap svg foreignObject div,
        .markmap-wrap svg foreignObject span {
          color: var(--color-text) !important;
          fill: var(--color-text) !important;
          font-family: ui-sans-serif, system-ui, sans-serif !important;
          font-size: 14px !important;
        }
        .markmap-wrap svg path {
          stroke: var(--color-accent) !important;
          stroke-width: 2px;
          opacity: 0.6;
        }
        .markmap-wrap svg circle {
          stroke: var(--color-accent) !important;
          stroke-width: 2px;
          fill: var(--color-surface) !important;
        }
      `}</style>

      <div className="markmap-wrap absolute inset-0 w-full h-full">
        <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
      </div>

      <div className="absolute bottom-3 right-3 flex items-center gap-1.5 bg-[var(--color-surface)] border border-[var(--color-border)] p-1.5 rounded-lg shadow-sm">
        <button
          onClick={handleZoomIn}
          className="p-1.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg)] rounded transition-colors"
          title="放大"
        >
          <ZoomIn size={14} />
        </button>
        <button
          onClick={handleZoomOut}
          className="p-1.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg)] rounded transition-colors"
          title="缩小"
        >
          <ZoomOut size={14} />
        </button>
        <button
          onClick={handleFit}
          className="p-1.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg)] rounded transition-colors"
          title="适应屏幕"
        >
          <Maximize2 size={14} />
        </button>
      </div>
    </div>
  );
}
