import { Link } from "react-router";
import { Page } from "../components/Layout";

export function NotFound() {
  return (
    <Page>
      <div className="py-24">
        <p className="font-mono text-sm text-faint">404</p>
        <h1 className="mt-2 font-display text-3xl font-black">Nothing on call here.</h1>
        <Link to="/" className="mt-4 inline-block text-sm text-muted underline">Back to the start page</Link>
      </div>
    </Page>
  );
}
