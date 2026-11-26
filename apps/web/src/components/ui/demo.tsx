import { Reshaped } from "reshaped/bundle";
import Checkbox from "@/components/ui/reshaped-checkbox";

export default function Demo() {
  return (
    <Reshaped theme="slate" colorMode="dark" scoped>
      <div
        style={{
          margin: "0 auto",
          padding: 24,
          display: "flex",
          justifyContent: "center",
        }}
      >
        <Checkbox
          name="animal"
          value="dog"
          onChange={({ value, name, event }) =>
            console.log({ value, name, event })
          }
        >
          Dog
        </Checkbox>
      </div>
    </Reshaped>
  );
}
