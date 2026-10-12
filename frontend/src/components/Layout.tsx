import { Outlet } from "react-router-dom";

import Header from "./Header";
import JiraBanner from "./JiraBanner";

export default function Layout() {
  return (
    <>
      <Header />
      <JiraBanner />
      <main className="content">
        <Outlet />
      </main>
    </>
  );
}
