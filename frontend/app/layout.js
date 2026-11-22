import "./style.css";

export const metadata = {
  title: "Meridian | Paper Trading",
  description: "Monitor automated US equity and crypto paper trading.",
};

export default function Layout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}
