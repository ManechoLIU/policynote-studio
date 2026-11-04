import type { CSSProperties, ReactNode } from 'react';

export type ReferencePage = 'home' | 'chat' | 'skills' | 'outputs' | 'accounts' | 'profile';
type Crop = readonly [number, number, number, number];
const ART: Record<ReferencePage, Crop> = {
  home: [750, 70, 780, 342], chat: [1090, 85, 440, 285],
  skills: [900, 65, 620, 220], outputs: [1010, 65, 510, 184],
  accounts: [915, 66, 390, 138], profile: [275, 70, 195, 145],
};
/** Original approved pixels, displayed through a real DOM viewport; no raster redraw. */
export function ReferenceArtwork({ page, crop = ART[page], className = '', label = '' }: {
  page: ReferencePage; crop?: Crop; className?: string; label?: string;
}) {
  const [x, y, width, height] = crop;
  const imageStyle: CSSProperties = { position: 'absolute', width: `${1536 / width * 100}%`, maxWidth: 'none', height: `${1024 / height * 100}%`, left: `${-x / width * 100}%`, top: `${-y / height * 100}%` };
  return <div className={`reference-art ${className}`} style={{ aspectRatio: `${width}/${height}`, position: 'relative', overflow: 'hidden' }} aria-hidden={label ? undefined : true} role={label ? 'img' : undefined} aria-label={label || undefined}>
    <img src={`./static/mingya-reference/${page}.png`} alt="" style={imageStyle} draggable={false} />
  </div>;
}
/** One complete cleaned banner per page; native text and controls remain above it. */
export function BannerBackground({ page, className = '' }: { page: ReferencePage; className?: string }) {
  const imageStyle: CSSProperties | undefined = page === 'profile' ? { borderImageSource: 'url("./static/mingya-banners/profile.png")' } : undefined;
  return <div className={`mingya-banner-background mingya-banner-background-${page} ${className}`} style={imageStyle} aria-hidden="true">
    {page !== 'profile' && <img src={`./static/mingya-banners/${page}.png`} alt="" draggable={false} />}
  </div>;
}
export function SectionMark({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <span className={`reference-section-mark ${className}`}><span className="reference-gold-rule" aria-hidden="true" />{children}</span>;
}
export function PageBanner({ page, title, subtitle, children, actions, className = '' }: {
  page: Exclude<ReferencePage, 'home' | 'chat'>; title: ReactNode; subtitle?: ReactNode;
  children?: ReactNode; actions?: ReactNode; className?: string;
}) {
  return <header className={`reference-page-banner reference-banner-${page} ${className}`}>
    <BannerBackground page={page} />
    {page === 'profile' && <div className="reference-banner-art profile-art-space" aria-hidden="true" />}
    <div className="reference-banner-copy"><h1><SectionMark>{page === 'outputs' ? <span className="outputs-banner-title">{title}<span className="outputs-title-underline" aria-hidden="true" /></span> : title}</SectionMark></h1>{subtitle && <p>{subtitle}</p>}{children}</div>
    {actions && <div className="reference-banner-actions">{actions}</div>}
  </header>;
}

export function WorkspaceGlyph({ kind, size = 26 }: { kind: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    home: <><path d="m3 10 9-8 9 8v11H3Z" /><path d="M9 21v-8h6v8" /></>,
    write: <><path d="m4 16 1-5L16 1l6 6L11 18l-5 1Z" /><path d="M3 23h19M14 4l6 6" /></>,
    layers: <><path d="m12 2 11 6-11 6L1 8Z" /><path d="m1 12 11 6 11-6M1 17l11 6 11-6" /></>,
    user: <><circle cx="12" cy="7" r="4" /><path d="M3 23v-4a6 6 0 0 1 6-6h6a6 6 0 0 1 6 6v4" /></>,
    star: <path d="m12 1 3.4 7 7.6 1.1-5.5 5.4 1.3 7.6-6.8-3.6-6.8 3.6 1.3-7.6L1 9.1 8.6 8Z" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind] || paths.layers}</svg>;
}
