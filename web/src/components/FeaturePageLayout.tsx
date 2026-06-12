import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface FeaturePageLayoutProps {
  title: string;
  iconClassName: string;
  children: ReactNode;
  headerActions?: ReactNode;
  mainClassName?: string;
}

const baseContainerClassName =
  "min-h-screen bg-gradient-to-b from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-800";
const baseHeaderClassName =
  "bg-white dark:bg-gray-800 shadow-md backdrop-blur-sm bg-opacity-80 dark:bg-opacity-80 sticky top-0 z-10";
const baseHeaderContentClassName =
  "max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex justify-between items-center";
const baseMainClassName = "mx-auto w-full pt-2 pb-2";
const defaultMainWidthClassName = "max-w-5xl px-4 sm:px-6 lg:px-8";
const backLinkClassName =
  "text-blue-600 dark:text-blue-400 mr-4 p-2 rounded-full hover:bg-blue-50 dark:hover:bg-gray-700 transition-colors";
const titleClassName = "text-2xl font-bold text-blue-700 dark:text-blue-300 flex items-center";

export function FeaturePageLayout({
  title,
  iconClassName,
  children,
  headerActions,
  mainClassName,
}: FeaturePageLayoutProps) {
  return (
    <div className={baseContainerClassName}>
      {/* <header className={baseHeaderClassName}>
        <div className={baseHeaderContentClassName}>
          <div className="flex items-center">
            <Link to="/" className={backLinkClassName}>
              <i className="fa-solid fa-arrow-left"></i>
            </Link>
            <h1 className={titleClassName}>
              <i className={`${iconClassName} mr-2`}></i>
              {title}
            </h1>
          </div>
          {headerActions}
        </div>
      </header> */}
      <main className={mainClassName ?? `${baseMainClassName} ${defaultMainWidthClassName}`}>
        {children}
      </main>
    </div>
  );
}

export default FeaturePageLayout;
