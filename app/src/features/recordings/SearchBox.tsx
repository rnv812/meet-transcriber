export function SearchBox({ value, onChange }: { value: string; onChange: (q: string) => void }) {
  return (
    <input
      type="search"
      className="search"
      role="searchbox"
      placeholder="Поиск"
      title="Поиск по названиям и тексту расшифровок"
      aria-label="Поиск по записям"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
