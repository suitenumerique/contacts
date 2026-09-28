import { PropsWithChildren } from "react";

export const GenericDisclaimer = ({
  message,
  imageSrc,
  children,
}: {
  message: string;
  imageSrc: string;
} & PropsWithChildren) => {
  return (
    <div className="contacts__generic-disclaimer">
      <div className="contacts__generic-disclaimer__content">
        <img className="contacts__generic-disclaimer__content__image" src={imageSrc} alt="" />
        <p>{message}</p>
        {children}
      </div>
    </div>
  );
};
