import clsx from "clsx";

interface TwoPaneProps {
  list: React.ReactNode;
  detail: React.ReactNode;
  /** On narrow screens only one pane shows: the detail once something is open. */
  hasSelection: boolean;
}

export const TwoPane = ({ list, detail, hasSelection }: TwoPaneProps) => (
  <div className={clsx("two-pane", { "two-pane--has-selection": hasSelection })}>
    <section className="two-pane__list">{list}</section>
    <section className="two-pane__detail">{detail}</section>
  </div>
);
