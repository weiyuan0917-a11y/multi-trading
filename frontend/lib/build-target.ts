export function isCustomerBuild() {
  return process.env.MT_BUILD_TARGET === "customer" || process.env.NEXT_PUBLIC_MT_BUILD_TARGET === "customer";
}

export function customerDisabledResponse() {
  return Response.json({ ok: false, error: "not_available_in_customer_build" }, { status: 404 });
}
