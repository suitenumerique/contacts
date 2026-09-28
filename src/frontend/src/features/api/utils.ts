export const getOrigin = () => {
  return (
    import.meta.env.VITE_API_ORIGIN || (typeof window !== "undefined" ? window.location.origin : "")
  );
};
export const baseApiUrl = (apiVersion: string = "1.0") => {
  const origin = getOrigin();
  return `${origin}/api/v${apiVersion}/`;
};

export const isJson = (str: string) => {
  try {
    JSON.parse(str);
  } catch {
    return false;
  }
  return true;
};
