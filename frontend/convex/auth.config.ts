const clerkDomain = process.env.CLERK_JWT_ISSUER_DOMAIN;

export default {
  providers: clerkDomain
    ? [
        {
          domain: clerkDomain,
          applicationID: "convex",
        },
      ]
    : [],
};
