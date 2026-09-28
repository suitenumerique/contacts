import { LaGaufreV2 } from "@gouvfr-lasuite/ui-components";
import { useConfig } from "@/features/config/ConfigProvider";

const DEFAULT_WIDGET_PATH = "https://static.suite.anct.gouv.fr/widgets/lagaufre.js";
const DEFAULT_API_URL = "https://lasuite.numerique.gouv.fr/api/services";

export const Gaufre = () => {
  const { config } = useConfig();

  if (!config.FRONTEND_LAGAUFRE_ENABLED) {
    return null;
  }

  return (
    <LaGaufreV2
      widgetPath={config.FRONTEND_LAGAUFRE_WIDGET_PATH || DEFAULT_WIDGET_PATH}
      apiUrl={config.FRONTEND_LAGAUFRE_WIDGET_API_URL || DEFAULT_API_URL}
    />
  );
};
