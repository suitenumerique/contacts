import { ToastContainer, toast } from "react-toastify";

export const Toaster = () => {
  return <ToastContainer />;
};

export const ToasterItem = ({
  children,
  type = "info",
}: {
  children: React.ReactNode;
  type?: "error" | "info" | "success" | "warning";
}) => {
  return (
    <div className={`suite__toaster__item suite__toaster__item--${type}`}>
      <div className="suite__toaster__item__content">{children}</div>
    </div>
  );
};

export const addToast = (children: React.ReactNode, options: Parameters<typeof toast>[1] = {}) => {
  return toast(children, {
    position: "bottom-center",
    closeButton: false,
    className: "suite__toaster__wrapper",
    autoClose: 8000,
    hideProgressBar: true,
    ...options,
  });
};

export const addErrorToast = (message: string) => {
  return addToast(<ToasterItem type="error">{message}</ToasterItem>);
};

export const addSuccessToast = (message: string) => {
  return addToast(<ToasterItem type="success">{message}</ToasterItem>, {
    autoClose: 4000,
  });
};
